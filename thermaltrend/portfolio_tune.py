"""Phase A tuning gate for the core-only portfolio system.

Step 4 of the portfolio plan. The core-only system (SPY index-timing sleeve
plus the shared risk overlay) is the base strategy; the satellite lane was
shelved after the point-in-time validation failed. This module tunes ONLY the
risk knobs that actually move the loss envelope on the core-only system and
freezes the winner so it can be deployed as ``live_config``.

Method (walk-forward, no look-ahead):
    - Roll train/test windows over the real trading calendar (backwards from
      the freshest data).
    - On each TRAIN window pick the config maximizing ``objective``
      (Calmar) subject to the loss envelope: the worst rolling 12-month
      return must be no worse than -``envelope`` (-5%).
    - Score that config on the immediately following, held-out TEST window.
    - Concatenate the held-out segments into one out-of-sample equity curve.
    - Re-fit on the most recent train window (ending at the freshest date)
      and freeze that config.

All tuning runs use a conservative slippage assumption and a cash yield equal
to the risk-free rate. The knobs are limited to those that are not inert for
core-only (``core_target_weight``, ``core_momentum_lookback``, ``breaker_pct``);
the exposure band, principal sleeve and regime thresholds do not bind without a
satellite lane.

Usage:
    python thermaltrend/portfolio_tune.py
    python thermaltrend/portfolio_tune.py --train-days 2520 --test-days 756 \\
        --grid '{"core_target_weight": [0.3, 0.4], "breaker_pct": [0.05, 0.08]}' \\
        --out thermaltrend/portfolio/live_config.json
"""

import argparse
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd

from thermaltrend.analytics.metrics import RISK_FREE_RATE, TRADING_DAYS_PER_YEAR
from thermaltrend.backtest import DEFAULT_DATA_DIR
from thermaltrend.feed import DataFeed
from thermaltrend.portfolio.config import PortfolioConfig, save_config
from thermaltrend.portfolio_backtest import run_portfolio_backtest
from thermaltrend.walk_forward import build_windows, expand_grid

DEFAULT_LIVE_CONFIG = (
    Path(__file__).resolve().parent / "portfolio" / "live_config.json"
)

ENVELOPE = 0.05
REALISTIC_SLIPPAGE_BPS = 10.0

DEFAULT_TRAIN_DAYS = 2520  # ~10 years
DEFAULT_TEST_DAYS = 756  # ~3 years
DEFAULT_STEP_DAYS = 756  # non-overlapping test windows
DEFAULT_WARMUP_DAYS = 252  # indicator warmup before each window

OBJECTIVE = "calmar"

# Only knobs that bind for the core-only system. band_min/band_max,
# principal_start_pct and the regime thresholds are (near-)inert without a
# satellite lane, so tuning them would be noise.
DEFAULT_GRID = {
    "core_target_weight": [0.25, 0.30, 0.35, 0.40, 0.50],
    "core_momentum_lookback": [126, 189, 252],
    "breaker_pct": [0.04, 0.05, 0.06, 0.08],
}


@dataclass
class TuneResult:
    """Result of ``run_portfolio_tune``.

    Attributes:
        windows: Scheduler window dicts (see ``walk_forward.build_windows``).
        rows: One row per window: chosen config per train window and its
            in-sample / held-out metrics.
        grid: Fully expanded list of config combos tuned.
        oos_equity_curve: Equity curve chained from the held-out segments.
        oos_metrics: Aggregate metrics over the chained held-out curve.
        frozen_config: Config selected on the most recent train window.
        frozen_metrics: Metrics of the frozen config over that train window.
        full_metrics: Metrics of the frozen config over the full history.
        envelope_met: True when every measured worst-12M (held-out and
            full-history) sits inside the envelope.
        envelope: The envelope used (positive fraction, e.g. 0.05).
    """

    windows: list[dict]
    rows: pd.DataFrame
    grid: list[dict]
    oos_equity_curve: pd.Series
    oos_metrics: dict
    frozen_config: PortfolioConfig
    frozen_metrics: dict
    full_metrics: dict
    envelope_met: bool
    envelope: float


def base_config(
    slippage_bps: float = REALISTIC_SLIPPAGE_BPS,
    cash_yield: float = RISK_FREE_RATE,
) -> PortfolioConfig:
    """Core-only base config: realistic slippage, risk-free cash yield."""
    return replace(
        PortfolioConfig(), slippage_bps=slippage_bps, cash_yield=cash_yield
    )


def segment_metrics(equity: pd.Series, start, end) -> dict:
    """Equity-curve metrics for the [start, end] slice.

    Mirrors the manager's worst-12-month definition (rolling 252 trading
    days) and the aggregate Sharpe (daily excess return vs the risk-free
    rate). Slice-based, so it can score a sub-window of a longer run without
    restarting the account.
    """
    empty = {
        "cagr": 0.0,
        "sharpe": 0.0,
        "max_drawdown": 0.0,
        "worst_12m": None,
        "calmar": 0.0,
        "total_return": 0.0,
    }
    if len(equity) < 2 or not isinstance(equity.index, pd.DatetimeIndex):
        return empty

    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    seg = equity[(equity.index >= start) & (equity.index <= end)]
    if len(seg) < 2:
        return empty

    first = float(seg.iloc[0])
    last = float(seg.iloc[-1])
    if first <= 0:
        return empty
    total_return = last / first - 1.0
    years = (seg.index[-1] - seg.index[0]).days / 365.25
    cagr = (last / first) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    max_drawdown = float((seg / seg.cummax() - 1.0).min())

    returns = seg.pct_change().dropna()
    std = float(returns.std())
    if std > 0:
        excess = returns - RISK_FREE_RATE / TRADING_DAYS_PER_YEAR
        sharpe = float(excess.mean()) / std * math.sqrt(TRADING_DAYS_PER_YEAR)
    else:
        sharpe = 0.0

    rolling = seg / seg.shift(TRADING_DAYS_PER_YEAR) - 1.0
    worst = rolling.min()
    worst_12m = None if pd.isna(worst) else float(worst)
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 else 0.0

    return {
        "cagr": round(cagr, 6),
        "sharpe": round(sharpe, 6),
        "max_drawdown": round(max_drawdown, 6),
        "worst_12m": None if worst_12m is None else round(worst_12m, 6),
        "calmar": round(calmar, 6),
        "total_return": round(total_return, 6),
    }


def select_train_config(
    candidates: list[tuple[dict, dict]],
    objective: str = OBJECTIVE,
    envelope: float = ENVELOPE,
) -> tuple[dict, dict, bool]:
    """Pick the best train config, preferring those inside the envelope.

    Args:
        candidates: (combo, train-metrics) pairs.
        objective: Metric to maximize among eligible configs.
        envelope: Positive loss envelope; eligible configs have
            ``worst_12m >= -envelope``.

    Returns:
        (best_combo, best_metrics, envelope_met). When no config meets the
        envelope, the config with the least-bad worst-12M is returned and
        ``envelope_met`` is False.
    """
    if not candidates:
        raise ValueError("no candidate configs to select from")

    def objective_value(metrics: dict) -> float:
        value = metrics.get(objective)
        return value if isinstance(value, (int, float)) else float("-inf")

    meeting = [
        (combo, m)
        for combo, m in candidates
        if m.get("worst_12m") is not None and m["worst_12m"] >= -envelope
    ]
    if meeting:
        best = max(meeting, key=lambda cm: objective_value(cm[1]))
        return best[0], best[1], True

    def worst_value(metrics: dict) -> float:
        value = metrics.get("worst_12m")
        return value if isinstance(value, (int, float)) else float("-inf")

    best = max(
        candidates, key=lambda cm: (worst_value(cm[1]), objective_value(cm[1]))
    )
    return best[0], best[1], False


def _key(combo: dict) -> tuple:
    return tuple(sorted(combo.items()))


def _run_combo(data_dir, combo, base, warmup_start, end) -> pd.Series:
    cfg = replace(base, **combo)
    result = run_portfolio_backtest(
        None,
        [],
        start_date=str(warmup_start.date()),
        end_date=str(end.date()),
        data_dir=data_dir,
        config=cfg,
        satellite=False,
    )
    return result.equity_curve


def _fit_window(data_dir, combos, base, warmup_start, train_start, train_end,
                objective, envelope, end=None):
    """Fit on [train_start, train_end]; winner's equity runs through ``end``.

    ``end`` (default: ``train_end``) lets the caller carry the winner's equity
    curve into the held-out test segment in the same run.
    """
    run_end = end if end is not None else train_end
    candidates = []
    equities = {}
    for combo in combos:
        equity = _run_combo(data_dir, combo, base, warmup_start, run_end)
        equities[_key(combo)] = equity
        candidates.append(
            (combo, segment_metrics(equity, train_start, train_end))
        )
    best_combo, best_metrics, met = select_train_config(
        candidates, objective, envelope
    )
    return best_combo, best_metrics, met, equities[_key(best_combo)]


def run_portfolio_tune(
    data_dir: str = DEFAULT_DATA_DIR,
    grid: dict | None = None,
    train_days: int = DEFAULT_TRAIN_DAYS,
    test_days: int = DEFAULT_TEST_DAYS,
    step_days: int = DEFAULT_STEP_DAYS,
    warmup_days: int = DEFAULT_WARMUP_DAYS,
    envelope: float = ENVELOPE,
    slippage_bps: float = REALISTIC_SLIPPAGE_BPS,
    cash_yield: float = RISK_FREE_RATE,
    capital: float | None = None,
    objective: str = OBJECTIVE,
) -> TuneResult:
    """Run the Phase A tuning gate for the core-only portfolio system."""
    grid = grid or DEFAULT_GRID
    combos = expand_grid(grid)
    base = base_config(slippage_bps, cash_yield)
    if capital is not None:
        base = replace(base, capital=capital)

    feed = DataFeed(data_dir=data_dir, tickers=[base.core_ticker])
    if len(feed) == 0:
        raise ValueError("No data found for the core ticker")
    dates = feed.dates

    windows = build_windows(dates, train_days, test_days, step_days, warmup_days)
    if not windows:
        raise ValueError(
            "Not enough data for the configured train/test windows "
            f"(train {train_days} + test {test_days} trading days)"
        )

    rows = []
    chain_parts = []
    level = base.capital
    for w in windows:
        best_combo, train_m, met, best_equity = _fit_window(
            data_dir, combos, base, w["warmup_start"], w["train_start"],
            w["train_end"], objective, envelope, end=w["test_end"],
        )
        oos_m = segment_metrics(best_equity, w["test_start"], w["test_end"])

        seg = best_equity[
            (best_equity.index >= w["test_start"])
            & (best_equity.index <= w["test_end"])
        ]
        if len(seg) >= 2:
            seg = seg / float(seg.iloc[0]) * level
            if chain_parts and seg.index[0] == chain_parts[-1].index[-1]:
                seg = seg.iloc[1:]
            chain_parts.append(seg)
            level = float(seg.iloc[-1])

        rows.append(
            {
                "window": f"{w['test_start'].date()} → {w['test_end'].date()}",
                "best_config": ", ".join(
                    f"{k}={v}" for k, v in sorted(best_combo.items())
                ),
                "envelope_met": met,
                "is_cagr": train_m["cagr"],
                "is_calmar": train_m["calmar"],
                "is_worst_12m": train_m["worst_12m"],
                "oos_cagr": oos_m["cagr"],
                "oos_sharpe": oos_m["sharpe"],
                "oos_max_drawdown": oos_m["max_drawdown"],
                "oos_worst_12m": oos_m["worst_12m"],
                "oos_calmar": oos_m["calmar"],
            }
        )

    if not rows:
        raise ValueError("No windows produced results")

    if chain_parts:
        oos_equity = pd.concat(chain_parts)
        oos_equity = oos_equity[
            ~oos_equity.index.duplicated(keep="last")
        ].sort_index()
        oos_metrics = segment_metrics(
            oos_equity, oos_equity.index[0], oos_equity.index[-1]
        )
    else:
        oos_equity = pd.Series(dtype=float)
        oos_metrics = segment_metrics(oos_equity, dates[-1], dates[-1])

    n = len(dates)
    refit_start_pos = max(0, n - train_days)
    warm_start_pos = max(0, refit_start_pos - warmup_days)
    frozen_combo, _, frozen_met, frozen_equity = _fit_window(
        data_dir, combos, base, dates[warm_start_pos], dates[refit_start_pos],
        dates[-1], objective, envelope,
    )
    frozen_config = replace(base, **frozen_combo)
    frozen_metrics = segment_metrics(frozen_equity, dates[refit_start_pos], dates[-1])

    full_equity = _run_combo(data_dir, frozen_combo, base, dates[0], dates[-1])
    full_metrics = segment_metrics(full_equity, dates[0], dates[-1])

    worst_values = [
        m
        for m in (
            oos_metrics["worst_12m"],
            frozen_metrics["worst_12m"],
            full_metrics["worst_12m"],
        )
        if m is not None
    ]
    envelope_met = frozen_met and all(m >= -envelope for m in worst_values)

    return TuneResult(
        windows=windows,
        rows=pd.DataFrame(rows),
        grid=combos,
        oos_equity_curve=oos_equity,
        oos_metrics=oos_metrics,
        frozen_config=frozen_config,
        frozen_metrics=frozen_metrics,
        full_metrics=full_metrics,
        envelope_met=envelope_met,
        envelope=envelope,
    )


def _fmt_pct(value, decimals: int = 1) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return "N/A"
    return f"{value * 100:.{decimals}f}%"


def _fmt_num(value, decimals: int = 2) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return "N/A"
    return f"{value:.{decimals}f}"


def format_tune_table(result: TuneResult) -> str:
    """Render a TuneResult as a readable terminal report."""
    lines = ["", "Phase A tuning gate — core-only SPY system"]
    lines.append(
        f"{len(result.rows)} windows, {len(result.grid)} combos, "
        f"envelope −{result.envelope * 100:.0f}% worst-12M"
    )

    width = max(21, result.rows["best_config"].str.len().max())
    header = (
        f"{'Test window':<23}  {'Best config':<{width}}  "
        f"{'IS Calmar':>9}  {'IS 12M':>7}  {'OOS CAGR':>8}  "
        f"{'OOS Sharpe':>10}  {'OOS MaxDD':>9}  {'OOS 12M':>7}  {'OOS Calmar':>10}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for _, row in result.rows.iterrows():
        flag = "" if row["envelope_met"] else " *"
        lines.append(
            f"{row['window']:<23}  {row['best_config']:<{width}}  "
            f"{_fmt_num(row['is_calmar']):>9}  {_fmt_pct(row['is_worst_12m']):>7}  "
            f"{_fmt_pct(row['oos_cagr']):>8}  {_fmt_num(row['oos_sharpe']):>10}  "
            f"{_fmt_pct(row['oos_max_drawdown']):>9}  "
            f"{_fmt_pct(row['oos_worst_12m']):>7}  "
            f"{_fmt_num(row['oos_calmar']):>10}{flag}"
        )
    lines.append("=" * len(header))

    m = result.oos_metrics
    lines.append(
        f"Held-out OOS:  CAGR {_fmt_pct(m['cagr'])}  Sharpe {_fmt_num(m['sharpe'])}  "
        f"MaxDD {_fmt_pct(m['max_drawdown'])}  Worst 12M {_fmt_pct(m['worst_12m'])}  "
        f"Calmar {_fmt_num(m['calmar'])}"
    )
    f = result.frozen_config
    fm = result.frozen_metrics
    lines.append(
        f"Frozen config: core_target_weight {f.core_target_weight:.2f}  "
        f"core_momentum_lookback {f.core_momentum_lookback}  "
        f"breaker_pct {f.breaker_pct:.2f}  "
        f"(refit CAGR {_fmt_pct(fm['cagr'])}, worst 12M {_fmt_pct(fm['worst_12m'])})"
    )
    full = result.full_metrics
    lines.append(
        f"Frozen full history:  CAGR {_fmt_pct(full['cagr'])}  "
        f"MaxDD {_fmt_pct(full['max_drawdown'])}  "
        f"Worst 12M {_fmt_pct(full['worst_12m'])}  Calmar {_fmt_num(full['calmar'])}"
    )
    verdict = "PASS" if result.envelope_met else "FAIL"
    lines.append(f"Envelope −{result.envelope * 100:.0f}%:  {verdict}")
    lines.append("")
    lines.append("* = no train config met the envelope; least-bad config chosen.")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Phase A tuning gate for the core-only portfolio system"
    )
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument(
        "--grid", default=None,
        help='Config grid as JSON (default: the built-in core-only grid)',
    )
    parser.add_argument("--train-days", type=int, default=DEFAULT_TRAIN_DAYS)
    parser.add_argument("--test-days", type=int, default=DEFAULT_TEST_DAYS)
    parser.add_argument("--step-days", type=int, default=DEFAULT_STEP_DAYS)
    parser.add_argument("--warmup-days", type=int, default=DEFAULT_WARMUP_DAYS)
    parser.add_argument("--envelope", type=float, default=ENVELOPE)
    parser.add_argument("--slippage-bps", type=float, default=REALISTIC_SLIPPAGE_BPS)
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--objective", default=OBJECTIVE)
    parser.add_argument(
        "--out", default=str(DEFAULT_LIVE_CONFIG),
        help="Where to write the frozen live config JSON",
    )
    parser.add_argument(
        "--no-freeze", action="store_true",
        help="Report only; do not write the live config",
    )
    parser.add_argument("--output", default=None, help="Export the full report to JSON")
    args = parser.parse_args()

    grid = json.loads(args.grid) if args.grid else None

    result = run_portfolio_tune(
        data_dir=args.data_dir,
        grid=grid,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
        warmup_days=args.warmup_days,
        envelope=args.envelope,
        slippage_bps=args.slippage_bps,
        capital=args.capital,
        objective=args.objective,
    )
    print(format_tune_table(result))

    if not args.no_freeze:
        save_config(result.frozen_config, args.out)
        print(f"Frozen live config written to {args.out}")

    if args.output:
        out = Path(args.output)
        out.write_text(
            json.dumps(
                {
                    "windows": result.rows.to_dict("records"),
                    "oos_metrics": result.oos_metrics,
                    "frozen_config": result.frozen_config.to_dict(),
                    "frozen_metrics": result.frozen_metrics,
                    "full_metrics": result.full_metrics,
                    "envelope": result.envelope,
                    "envelope_met": result.envelope_met,
                },
                indent=2,
                default=str,
            )
            + "\n"
        )
        print(f"Report exported to {out}")


if __name__ == "__main__":
    main()
