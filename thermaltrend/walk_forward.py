"""Walk-forward (rolling out-of-sample) validation for strategies.

Walk-forward analysis is the closest simulation of live trading: for each
rolling window the strategy parameters are optimized on a TRAIN period and
then scored on the immediately following, held-out TEST period. Nobody ever
sees a test segment before optimizing, so the concatenated out-of-sample
equity curve reflects how the strategy would have performed if you had
re-tuned it going forward.

Usage:
    python thermaltrend/walk_forward.py \\
        --strategy ma_crossover --tickers AAPL MSFT --start 2015-01-01 \\
        --grid '{"fast_period": [5, 10, 20, 50], "slow_period": [100, 200]}'
    python thermaltrend/walk_forward.py \\
        --strategy rsi_mean_reversion --ticker AAPL --start 2015-01-01 \\
        --grid '{"period": [10, 14, 20], "oversold": [20, 30], "overbought": [70, 80]}' \\
        --objective sharpe --output wf.json
"""

import itertools
import json
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from thermaltrend.analytics.compare import run_strategy_analysis
from thermaltrend.analytics.metrics import (
    compute_aggregate_metrics,
    compute_benchmark_metrics,
    compute_equity_curve,
)
from thermaltrend.analytics.report import export_json
from thermaltrend.analytics.trade_simulator import Trade
from thermaltrend.backtest import (
    DEFAULT_DATA_DIR,
    DEFAULT_MEMBERSHIP_PATH,
    DEFAULT_REMOVED_DATA_DIR,
    STRATEGIES,
    UNIVERSE_CHOICES,
)
from thermaltrend.core.engine import DataEngine
from thermaltrend.feed import DataFeed

DEFAULT_TRAIN_DAYS = 504  # ~2 years of trading days
DEFAULT_TEST_DAYS = 126  # ~6 months
DEFAULT_STEP_DAYS = 126  # non-overlapping test windows
DEFAULT_WARMUP_DAYS = 252  # history given to slow indicators before training

# Default param values per strategy. The grid's raw values are coerced to the
# type of these defaults so JSON numbers (e.g. 14.0 for `period`) don't crash
# strategy constructors that expect ints.
STRATEGY_DEFAULTS = {
    "ma_crossover": {"fast_period": 50, "slow_period": 200},
    "donchian": {"entry_period": 20, "exit_period": 10},
    "rsi_mean_reversion": {"period": 14, "oversold": 30.0, "overbought": 70.0},
    "atr_trailing_stop": {"entry_period": 20, "atr_period": 14, "atr_multiple": 3.0},
    "dual_momentum": {"lookback": 126, "benchmark_ticker": "SPY"},
    # window (252) + momentum_lookback (126) = 378 bars before the first signal.
    # weights is a nested dict — not grid-searchable; keep it fixed.
    "factor_scoring": {
        "momentum_lookback": 126,
        "volatility_lookback": 60,
        "trend_fast": 20,
        "trend_slow": 60,
        "reversion_lookback": 10,
        "window": 252,
        "entry_threshold": 0.6,
        "exit_threshold": 0.5,
        "absolute_momentum": True,
    },
}

# Strategy parameters that gate a strategy's first signal by lookback. Used to
# warn when warmup / train windows are too short for the grid being tested.
STRATEGY_LOOKBACK_KEYS = {
    "ma_crossover": ["slow_period"],
    "donchian": ["entry_period"],
    "rsi_mean_reversion": ["period"],
    "atr_trailing_stop": ["entry_period", "atr_period"],
    "dual_momentum": ["lookback"],
    "factor_scoring": ["momentum_lookback", "window"],
}

OBJECTIVE_CHOICES = [
    "cagr", "sharpe", "sortino", "max_drawdown", "calmar",
    "win_rate", "total_trades",
]


@dataclass
class WalkForwardResult:
    """Result of a walk-forward run. See ``run_walk_forward``.

    Attributes:
        strategy_name: Strategy that was validated.
        tickers: Tickers traded.
        grid: Fully expanded list of parameter combos (coerced types).
        windows: Per-window scheduler rows as dicts
            (warmup_start / train_start / train_end / test_start / test_end).
        rows: DataFrame with one row per window. Columns: window (label),
            best_params (str), is_* (in-sample metrics), oos_* (held-out
            metrics), decay_<metric> (oos - is).
        metrics: Aggregate metrics over ALL out-of-sample trades.
        equity_curve: Equity curve built ONLY from out-of-sample trades.
        trades: All out-of-sample trades (one per window, non-overlapping).
        params_by_window: Best params (dict) per window, in window order.
        decay: Dict metric -> {"is_mean", "oos_mean", "decay"}.
    """

    strategy_name: str
    tickers: list[str]
    grid: list[dict]
    windows: list[dict]
    rows: pd.DataFrame
    metrics: dict
    equity_curve: pd.Series
    trades: list[Trade]
    params_by_window: list[dict]
    decay: dict


def expand_grid(grid: dict) -> list[dict]:
    """Expand a param-name -> list-of-values spec into the cartesian product.

    Args:
        grid: Dict mapping strategy parameter names to lists of candidate
            values, e.g. {"fast_period": [5, 20], "slow_period": [100, 200]}.

    Returns:
        List of one dict per combination.

    Raises:
        ValueError: If the grid is not a non-empty dict of non-empty lists.
    """
    if not isinstance(grid, dict) or not grid:
        raise ValueError(
            "grid must be a non-empty dict mapping param names to lists of values"
        )
    for key, values in grid.items():
        if not isinstance(values, (list, tuple)) or len(values) == 0:
            raise ValueError(
                f"grid param '{key}' must be a non-empty list of values"
            )
    keys = list(grid.keys())
    return [
        dict(zip(keys, combo)) for combo in itertools.product(*grid.values())
    ]


def _coerce_params(strategy_name: str, combo: dict) -> dict:
    """Validate a param combo against the strategy's known params and coerce
    JSON values (floats/strings) to the type of the strategy's defaults."""
    defaults = STRATEGY_DEFAULTS.get(strategy_name)
    if defaults is None:
        raise ValueError(
            f"Unknown strategy '{strategy_name}'. "
            f"Available: {', '.join(STRATEGIES.keys())}"
        )

    unknown = set(combo) - set(defaults)
    if unknown:
        raise ValueError(
            f"Unknown parameter(s) {', '.join(sorted(unknown))} for strategy "
            f"'{strategy_name}'. Known: {', '.join(sorted(defaults))}"
        )

    coerced = {}
    for key, value in combo.items():
        target = type(defaults[key])
        if target is int and isinstance(value, (float, str)):
            if isinstance(value, float) and not value.is_integer():
                raise ValueError(
                    f"param '{key}' for strategy '{strategy_name}' expects an "
                    f"int, got {value}"
                )
            value = int(value)
        elif target is float and isinstance(value, (int, str)):
            value = float(value)
        coerced[key] = value
    return coerced


def _max_lookback(strategy_name: str) -> int:
    """Largest lookback the strategy needs to emit its first signal."""
    lookbacks = {
        "ma_crossover": 200,
        "donchian": 20,
        "rsi_mean_reversion": 14,
        "atr_trailing_stop": 20,
        "dual_momentum": 126,
        # Requires a full normalization window + biggest factor lookback.
        "factor_scoring": 378,
    }
    return lookbacks.get(strategy_name, 0)


def build_windows(
    dates,
    train_days: int = DEFAULT_TRAIN_DAYS,
    test_days: int = DEFAULT_TEST_DAYS,
    step_days: int = DEFAULT_STEP_DAYS,
    warmup_days: int = DEFAULT_WARMUP_DAYS,
) -> list[dict]:
    """Schedule rolling train/test windows over the data's real trading calendar.

    Windows are built backwards from the last available date (so the final
    window uses the freshest data) and returned in chronological order. Each
    window is a dict::

        {
            "warmup_start": Timestamp,  # feed start (gives indicators history)
            "train_start":  Timestamp,
            "train_end":    Timestamp,  # last trading day before the test window
            "test_start":   Timestamp,
            "test_end":     Timestamp,  # last trading day of the test segment
        }

    A window is valid when ``test_start - train_days`` trading days exist before
    it. Test windows step forward by ``step_days`` (non-overlapping by default).

    Args:
        dates: Sequence of trading dates (any order; will be sorted).
        train_days: Length of the optimization (in-sample) window.
        test_days: Length of the held-out (out-of-sample) window.
        step_days: Number of trading days to roll forward between windows.
        warmup_days: Extra history prepended to the train window so slow
            indicators are warm before the optimization period begins.

    Returns:
        List of window dicts, empty if there is not enough data.
    """
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(list(dates))))
    n = len(dates)
    if n == 0:
        return []

    windows = []
    test_end_pos = n - 1
    while test_end_pos - test_days + 1 - train_days >= 0:
        test_start_pos = test_end_pos - test_days + 1
        train_end_pos = test_start_pos - 1
        train_start_pos = train_end_pos - train_days + 1
        warm_start_pos = max(0, train_start_pos - warmup_days)
        windows.append(
            {
                "warmup_start": dates[warm_start_pos],
                "train_start": dates[train_start_pos],
                "train_end": dates[train_end_pos],
                "test_start": dates[test_start_pos],
                "test_end": dates[test_end_pos],
            }
        )
        test_end_pos -= step_days

    windows.reverse()
    return windows


def _trades_in_window(trades: list[Trade], start, end) -> list[Trade]:
    """Trades whose exit date falls inside [start, end] (inclusive)."""
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    return [
        t for t in trades
        if start <= pd.Timestamp(t.exit_date) <= end
    ]


def _segment_metrics(trades: list[Trade], start, end) -> dict:
    """Aggregate metrics for trades that exited inside a date segment.

    Considers only trades closed inside [start, end] (by exit date), so a
    trade straddling the train/test boundary is scored in the test window
    only. Open (data_end) positions within the segment are counted but do
    not contribute to risk-adjusted metrics.
    """
    segment = _trades_in_window(trades, start, end)
    completed = [t for t in segment if t.exit_reason != "data_end"]
    equity_curve = None
    if completed:
        equity_curve = compute_equity_curve(
            segment, pd.Timestamp(start), end_date=pd.Timestamp(end)
        )
    return _sparse_risk_metrics(compute_aggregate_metrics(segment, equity_curve))


def _select_best(
    combo_results: list[tuple[dict, dict, dict]], objective: str
) -> tuple[dict, dict]:
    """Pick the combo with the best in-sample (train) objective metric.

    Args:
        combo_results: List of (combo, in_sample_metrics) tuples from
            ``_segment_metrics`` on the train segment.
        objective: Metric to optimize. Higher is always better — for every
            objective, the best value is the largest (a less negative drawdown
            is better than a deep one; more trades is more evidence). Combos
            with zero completed trades in the train segment are skipped
            (they cannot be evaluated); on a total tie the first combo wins.

    Returns:
        The winning (combo, in_sample_metrics) pair.
    """
    candidates = [
        (combo, m) for combo, m in combo_results if m.get("trades_completed", 0) > 0
    ]
    if not candidates:
        candidates = combo_results

    best_pair = None
    best_val = None
    for combo, m in candidates:
        val = m.get(objective)
        if val is None or (isinstance(val, float) and val != val):
            val = 0.0
        if best_pair is None or val > best_val:
            best_pair, best_val = (combo, m), val
    return best_pair


def _format_params(combo: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(combo.items()))


# Risk-adjusted metrics on windows with fewer completed trades than this are
# statistically meaningless (a handful of lump trades on a mostly-flat equity
# curve produces a degenerate daily-return Sharpe) and are reported as N/A.
MIN_TRADES_FOR_RISK_METRICS = 5
SPARSE_RISK_METRICS = ("sharpe", "sortino", "calmar")


def _sparse_risk_metrics(m: dict) -> dict:
    """Null out risk-adjusted metrics for sparse segments (< MIN trades).

    Segments with zero completed trades keep their empty-metric zeros; only
    tiny segments (fewer than ``MIN_TRADES_FOR_RISK_METRICS`` completed
    trades) get their noisy sharpe/sortino/calmar replaced with NaN so they
    are reported as N/A and excluded from decay means.
    """
    if 0 < m.get("trades_completed", 0) < MIN_TRADES_FOR_RISK_METRICS:
        for key in SPARSE_RISK_METRICS:
            m[key] = float("nan")
    return m


def _make_feed(
    data_dir,
    tickers: list[str],
    start_date: str | None,
    end_date: str | None,
    universe: str,
) -> DataFeed:
    kwargs = dict(
        data_dir=data_dir,
        tickers=tickers,
        start_date=start_date,
        end_date=end_date,
    )
    if universe == "point_in_time":
        kwargs.update(
            membership=DEFAULT_MEMBERSHIP_PATH,
            removed_data_dir=DEFAULT_REMOVED_DATA_DIR,
        )
    return DataFeed(**kwargs)


def run_walk_forward(
    strategy_name: str,
    tickers: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    grid: dict | None = None,
    train_days: int = DEFAULT_TRAIN_DAYS,
    test_days: int = DEFAULT_TEST_DAYS,
    step_days: int = DEFAULT_STEP_DAYS,
    warmup_days: int = DEFAULT_WARMUP_DAYS,
    objective: str = "sharpe",
    data_dir: str = DEFAULT_DATA_DIR,
    universe: str = "current",
) -> WalkForwardResult:
    """Run walk-forward validation for a single strategy. Library-friendly.

    For each rolling window the strategy is re-optimized over the TRAIN
    segment (choosing the combo with the best ``objective`` metric) and then
    scored on the immediately following, held-out TEST segment. All reported
    performance is out-of-sample.

    Args:
        strategy_name: One of ``STRATEGIES`` (see thermaltrend.backtest).
        tickers: Tickers to trade (add SPY for dual_momentum).
        start_date / end_date: Overall evaluation range (YYYY-MM-DD or None).
        grid: Param-name -> list-of-values spec (cartesian). Required.
        train_days: Optimization window length in trading days.
        test_days: Held-out window length in trading days.
        step_days: Trading days to roll forward between windows.
        warmup_days: Extra history for indicator warmup before each window.
        objective: Metric used to select the best combo on each train window.
        data_dir: Directory containing Parquet files.
        universe: "current" or "point_in_time" (see thermaltrend.backtest).

    Returns:
        WalkForwardResult with per-window rows, aggregate OOS metrics, the
        OOS-only equity curve, and IS->OOS decay summaries.

    Raises:
        ValueError: Unknown strategy, bad grid, bad objective, or insufficient
            data for at least one window.
    """
    if strategy_name not in STRATEGIES:
        raise ValueError(
            f"Unknown strategy '{strategy_name}'. "
            f"Available: {', '.join(STRATEGIES.keys())}"
        )
    if grid is None:
        raise ValueError(
            "grid is required — map each param to a list of values, "
            "e.g. {'fast_period': [5, 20], 'slow_period': [100, 200]}"
        )
    if objective not in OBJECTIVE_CHOICES:
        raise ValueError(
            f"Unknown objective '{objective}'. "
            f"Choose from: {', '.join(OBJECTIVE_CHOICES)}"
        )

    raw_combos = expand_grid(grid)
    combos = [_coerce_params(strategy_name, c) for c in raw_combos]

    base_feed = _make_feed(data_dir, tickers, start_date, end_date, universe)
    if len(base_feed) == 0:
        raise ValueError("No data found. Check your --tickers and date range.")

    windows = build_windows(
        base_feed.dates, train_days, test_days, step_days, warmup_days
    )
    if not windows:
        raise ValueError(
            "Not enough data for walk-forward: need at least "
            f"train_days ({train_days}) + test_days ({test_days}) trading days "
            f"in {start_date or 'start'} to {end_date or 'end'}."
        )

    max_lookback = _max_lookback(strategy_name)
    if warmup_days < max_lookback:
        warnings.warn(
            f"warmup_days ({warmup_days}) < the {max_lookback}-bar warmup "
            f"'{strategy_name}' needs before its first signal; early windows "
            "will have fewer available signals.",
            stacklevel=2,
        )

    rows = []
    oos_trades: list[Trade] = []
    params_by_window: list[dict] = []
    last_processed_window = None

    for w in windows:
        feed = _make_feed(
            data_dir,
            tickers,
            str(w["warmup_start"].date()),
            str(w["test_end"].date()),
            universe,
        )
        if len(feed) == 0:
            continue
        last_processed_window = w

        combo_results = []
        for combo in combos:
            strategy = STRATEGIES[strategy_name](dict(combo))
            engine = DataEngine(feed, strategy)
            signals = engine.run()
            result = run_strategy_analysis(
                signals, feed._data, strategy_name, membership=feed.membership
            )
            train_m = _segment_metrics(
                result["trades"], w["train_start"], w["train_end"]
            )
            combo_results.append((combo, result, train_m))

        best_combo, best_result, best_train_m = None, None, None
        # _select_best works on (combo, train_metrics) pairs
        pair = _select_best(
            [(combo, train_m) for combo, result, train_m in combo_results],
            objective,
        )
        best_combo, best_train_m = pair
        best_result = next(
            result for combo, result, train_m in combo_results if combo == best_combo
        )

        oos_m = _segment_metrics(
            best_result["trades"], w["test_start"], w["test_end"]
        )

        rows.append(
            {
                "window": (
                    f"{w['test_start'].date()} → {w['test_end'].date()}"
                ),
                "best_params": _format_params(best_combo),
                "is_cagr": best_train_m["cagr"],
                "is_sharpe": best_train_m["sharpe"],
                "is_sortino": best_train_m["sortino"],
                "is_max_drawdown": best_train_m["max_drawdown"],
                "is_calmar": best_train_m["calmar"],
                "is_total_trades": best_train_m["trades_completed"],
                "oos_cagr": oos_m["cagr"],
                "oos_sharpe": oos_m["sharpe"],
                "oos_sortino": oos_m["sortino"],
                "oos_max_drawdown": oos_m["max_drawdown"],
                "oos_calmar": oos_m["calmar"],
                "oos_total_trades": oos_m["trades_completed"],
                "oos_total_return": oos_m["total_return"],
            }
        )
        oos_trades.extend(
            _trades_in_window(best_result["trades"], w["test_start"], w["test_end"])
        )
        params_by_window.append(best_combo)

    if not rows:
        raise ValueError(
            "No windows produced results. Check your data range and window sizes."
        )

    last_test_end = last_processed_window["test_end"]

    if oos_trades:
        first_entry = min(pd.Timestamp(t.entry_date) for t in oos_trades)
        equity_curve = compute_equity_curve(
            oos_trades, first_entry, end_date=last_test_end
        )
    else:
        equity_curve = pd.Series([100_000.0])
    metrics = compute_aggregate_metrics(oos_trades, equity_curve)

    decay = {}
    df_rows = pd.DataFrame(rows)
    for metric in ("cagr", "sharpe", "sortino", "calmar"):
        if f"is_{metric}" in df_rows.columns and f"oos_{metric}" in df_rows.columns:
            is_mean = float(df_rows[f"is_{metric}"].mean())
            oos_mean = float(df_rows[f"oos_{metric}"].mean())
            df_rows[f"decay_{metric}"] = df_rows[f"oos_{metric}"] - df_rows[
                f"is_{metric}"
            ]
            decay[metric] = {
                "is_mean": round(is_mean, 4),
                "oos_mean": round(oos_mean, 4),
                "decay": round(oos_mean - is_mean, 4),
            }

    return WalkForwardResult(
        strategy_name=strategy_name,
        tickers=list(tickers),
        grid=combos,
        windows=windows,
        rows=df_rows,
        metrics=metrics,
        equity_curve=equity_curve,
        trades=oos_trades,
        params_by_window=params_by_window,
        decay=decay,
    )


def _fmt_num(val, decimals: int = 2) -> str:
    """Format a float, rendering non-finite/NaN as N/A."""
    if val is None or (isinstance(val, float) and val != val):
        return "N/A"
    return f"{val:.{decimals}f}"


def _fmt_pct(val, decimals: int = 1) -> str:
    if val is None or (isinstance(val, float) and val != val):
        return "N/A"
    return f"{val * 100:.{decimals}f}%"


def format_walk_forward_table(result: WalkForwardResult) -> str:
    """Format a WalkForwardResult as a readable terminal table."""
    if result.rows.empty:
        return "No windows to report."

    r = result.rows
    lines = []
    lines.append("")
    lines.append(
        f"Walk-Forward: {result.strategy_name} on {', '.join(result.tickers)}"
    )
    last_window = result.windows[-1]
    start = r["window"].iloc[0].split(" → ")[0]
    lines.append(
        f"OOS range: {start} → {last_window['test_end'].date()} "
        f"({len(r)} windows, {len(result.grid)} combos)"
    )

    para_width = max(28, r["best_params"].str.len().max() + 2)
    col_w = 12
    columns = [
        ("Window", 23), ("Best params", para_width), ("IS Sharpe", col_w),
        ("OOS Sharpe", col_w), ("OOS CAGR", col_w), ("OOS MaxDD", col_w),
        ("OOS Trades", col_w),
    ]
    header = "  ".join(f"{name:>{width}s}" for name, width in columns)
    lines.append(header)
    lines.append("-" * len(header))

    row_fmt = "{:<23s}  {:<" + str(para_width) + "s}  " + "  ".join(
        ["{:" + str(col_w) + "s}"] * 5
    )

    for _, row in r.iterrows():
        lines.append(
            row_fmt.format(
                row["window"],
                row["best_params"],
                _fmt_num(row["is_sharpe"]),
                _fmt_num(row["oos_sharpe"]),
                _fmt_pct(row["oos_cagr"]),
                _fmt_pct(row["oos_max_drawdown"]),
                _fmt_num(row["oos_total_trades"], decimals=0),
            )
        )

    lines.append("=" * len(header))

    m = result.metrics
    lines.append(
        f"Overall OOS:  CAGR {_fmt_pct(m['cagr'])}  Sharpe {_fmt_num(m['sharpe'])}  "
        f"Sortino {_fmt_num(m['sortino'])}  MaxDD {_fmt_pct(m['max_drawdown'])}  "
        f"Calmar {_fmt_num(m['calmar'])}  Trades {m['trades_completed']}"
    )

    dec = []
    for metric in ("sharpe", "cagr"):
        d = result.decay.get(metric)
        if d:
            dec.append(
                f"{metric}: IS {_fmt_num(d['is_mean'])} -> OOS "
                f"{_fmt_num(d['oos_mean'])} (decay {_fmt_num(d['decay'])})"
            )
    lines.append("IS→OOS decay  " + " | ".join(dec))
    lines.append("")
    lines.append("* Every number above counts trades that exited inside a held-out test window.")
    lines.append(f"* N/A = fewer than {MIN_TRADES_FOR_RISK_METRICS} completed trades in the "
                 "window — too sparse for a reliable metric.")
    lines.append("")
    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Walk-forward (rolling out-of-sample) validation"
    )
    parser.add_argument(
        "--strategy", required=True, choices=list(STRATEGIES.keys()),
        help="Strategy to validate",
    )
    parser.add_argument("--ticker", default=None, help="Single ticker (shorthand)")
    parser.add_argument("--tickers", nargs="+", default=None, help="Multiple tickers")
    parser.add_argument("--start", default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--grid", required=True,
        help='Parameter grid as JSON, e.g. \'{"fast_period": [5, 20]}\'',
    )
    parser.add_argument(
        "--objective", default="sharpe", choices=OBJECTIVE_CHOICES,
        help="Metric to optimize on each train window (default: sharpe)",
    )
    parser.add_argument(
        "--train-days", type=int, default=DEFAULT_TRAIN_DAYS,
        help="Optimization window length in trading days (default: 504)",
    )
    parser.add_argument(
        "--test-days", type=int, default=DEFAULT_TEST_DAYS,
        help="Held-out window length in trading days (default: 126)",
    )
    parser.add_argument(
        "--step-days", type=int, default=DEFAULT_STEP_DAYS,
        help="Trading days to roll forward between windows (default: 126)",
    )
    parser.add_argument(
        "--warmup-days", type=int, default=DEFAULT_WARMUP_DAYS,
        help="Indicator warmup history before each window (default: 252)",
    )
    parser.add_argument(
        "--data-dir", default=DEFAULT_DATA_DIR,
        help="Directory containing Parquet files",
    )
    parser.add_argument(
        "--universe", default="current", choices=UNIVERSE_CHOICES,
        help="'point_in_time' restricts members to their actual S&P 500 "
             "membership windows (requires membership.csv)",
    )
    parser.add_argument(
        "--output", default=None, help="Export results to JSON"
    )
    args = parser.parse_args()

    tickers = args.tickers or ([args.ticker] if args.ticker else None)
    if not tickers:
        if args.universe == "point_in_time":
            membership = pd.read_csv(
                DEFAULT_MEMBERSHIP_PATH,
                parse_dates=["date_added", "date_removed"],
            )
            tickers = sorted(membership["ticker"].unique())
        else:
            parser.error("Provide --ticker or --tickers")

    try:
        grid = json.loads(args.grid)
    except json.JSONDecodeError as exc:
        parser.error(f"--grid is not valid JSON: {exc}")

    result = run_walk_forward(
        strategy_name=args.strategy,
        tickers=tickers,
        start_date=args.start,
        end_date=args.end,
        grid=grid,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
        warmup_days=args.warmup_days,
        objective=args.objective,
        data_dir=args.data_dir,
        universe=args.universe,
    )

    print(format_walk_forward_table(result))

    if args.output:
        out = Path(args.output)
        spy = None
        spy_path = Path(args.data_dir) / "SPY.parquet"
        if spy_path.exists():
            try:
                spy = compute_benchmark_metrics(
                    pd.read_parquet(spy_path),
                    result.rows["window"].iloc[0].split(" → ")[0],
                    result.windows[-1]["test_end"],
                )
            except Exception:
                spy = None

        data = {
            "strategy_name": result.strategy_name,
            "tickers": result.tickers,
            "windows": result.rows.to_dict("records"),
            "params_by_window": result.params_by_window,
            "metrics": result.metrics,
            "decay": result.decay,
            "benchmark": spy,
            "equity_curve": result.equity_curve,
            "trades": result.trades,
        }
        export_json(data, out)
        print(f"Results exported to {out}")


if __name__ == "__main__":
    main()