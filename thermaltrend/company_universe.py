"""Market-cap ranked company universe and per-company strategy drilldown.

Pure data layer behind the dashboard's "Company Universe" page:

* :func:`current_member_tickers` — S&P 500 members that have local price data.
* Market-cap handling — market caps are fetched from Yahoo Finance with
  yfinance and cached to ``market_cap.csv`` so ordering by market cap works
  fully offline afterwards. :func:`refresh_market_caps` merges a fresh fetch
  over the previous snapshot so tickers that fail to refresh keep their last
  known value.
* :func:`analyze_company` — runs every registered strategy on a single company
  and returns per-strategy metrics, trades, and equity curves, so the UI can
  show which strategies work well for which companies.

Importing this module has no Streamlit side effects; the dashboard's pages are
what render the results. There is also a small CLI to pre-fetch the market cap
cache: ``python -m thermaltrend.company_universe --refresh``.
"""

from __future__ import annotations

import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from thermaltrend.analytics.compare import run_strategy_analysis
from thermaltrend.core.engine import DataEngine
from thermaltrend.core.strategy import (
    ATRTrailingStopStrategy,
    DonchianBreakoutStrategy,
    DualMomentumStrategy,
    FactorScoringStrategy,
    MACrossoverStrategy,
    RSIMeanReversionStrategy,
)
from thermaltrend.feed import DataFeed
from thermaltrend.ticker_search import company_name

DEFAULT_DATA_DIR = Path(__file__).parent / "data" / "equities"
DEFAULT_REMOVED_DATA_DIR = Path(__file__).parent / "data" / "equities_removed"
DEFAULT_MEMBERSHIP_PATH = DEFAULT_DATA_DIR / "membership.csv"

MARKET_CAP_FILENAME = "market_cap.csv"

BENCHMARK_TICKER = "SPY"
DUAL_MOMENTUM_LABEL = "Dual Mom 126d"

# Same strategies (and defaults) the rest of the dashboard uses, so the
# per-company drilldown is directly comparable with the other pages.
STRATEGY_SPECS = [
    {
        "label": "MA 50/200",
        "cls": MACrossoverStrategy,
        "params": {"fast_period": 50, "slow_period": 200},
    },
    {
        "label": "Donchian 20/10",
        "cls": DonchianBreakoutStrategy,
        "params": {"entry_period": 20, "exit_period": 10},
    },
    {
        "label": "RSI 14",
        "cls": RSIMeanReversionStrategy,
        "params": {"period": 14, "oversold": 30.0, "overbought": 70.0},
    },
    {
        "label": "ATR Trail 20/14/3",
        "cls": ATRTrailingStopStrategy,
        "params": {"entry_period": 20, "atr_period": 14, "atr_multiple": 3.0},
    },
    {
        "label": DUAL_MOMENTUM_LABEL,
        "cls": DualMomentumStrategy,
        "params": {"lookback": 126, "benchmark_ticker": BENCHMARK_TICKER},
    },
    {
        "label": "Factor 126d",
        "cls": FactorScoringStrategy,
        "params": {
            "momentum_lookback": 126,
            "volatility_lookback": 60,
            "trend_fast": 20,
            "trend_slow": 60,
            "reversion_lookback": 10,
            "window": 252,
            "entry_threshold": 0.60,
            "exit_threshold": 0.50,
            "absolute_momentum": True,
        },
    },
]

DEFAULT_STRATEGY_LABELS = [spec["label"] for spec in STRATEGY_SPECS]

# A strategy needs at least this many completed trades before its metrics are
# treated as a meaningful track record for the recommendation.
MIN_TRADES_FOR_CONFIDENCE = 3


def current_member_tickers(
    data_dir: str | Path = DEFAULT_DATA_DIR,
    membership_path: str | Path = DEFAULT_MEMBERSHIP_PATH,
) -> list[str]:
    """Current S&P 500 members that have local price data, sorted.

    The SPY benchmark is excluded — it is an ETF, not a company, and it is
    injected automatically by the strategies that need it.
    """
    data_dir = Path(data_dir)
    membership_path = Path(membership_path)
    parquet_tickers = {p.stem for p in data_dir.glob("*.parquet")} - {BENCHMARK_TICKER}
    if not membership_path.exists():
        return sorted(parquet_tickers)
    frame = pd.read_csv(membership_path)
    current = frame[frame["is_current"].astype(str).str.lower() == "true"]["ticker"]
    return sorted(set(current) & parquet_tickers)


# ---------------------------------------------------------------------------
# Market caps
# ---------------------------------------------------------------------------


def market_caps_path(data_dir: str | Path = DEFAULT_DATA_DIR) -> Path:
    return Path(data_dir) / MARKET_CAP_FILENAME


def load_market_caps(data_dir: str | Path = DEFAULT_DATA_DIR) -> pd.DataFrame | None:
    """Market cap snapshot sorted in decreasing order, or None when unavailable.

    Returns a DataFrame with columns ``ticker``, ``market_cap``, ``fetched_at``
    (the day the snapshot was pulled). Returns None when no usable cache exists.
    """
    path = market_caps_path(data_dir)
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
    except Exception:
        return None
    if df.empty or "market_cap" not in df.columns:
        return None

    df = df[df["market_cap"].notna() & (df["market_cap"] > 0)].copy()
    if df.empty:
        return None

    if "fetched_at" not in df.columns:
        df["fetched_at"] = ""
    return df.sort_values("market_cap", ascending=False).reset_index(drop=True)


def save_market_caps(
    snapshot: dict[str, float],
    data_dir: str | Path = DEFAULT_DATA_DIR,
) -> Path:
    """Persist a {ticker: market_cap} snapshot to the cache CSV."""
    df = pd.DataFrame(
        [
            {
                "ticker": ticker,
                "market_cap": round(value, 2),
                "fetched_at": _fetched_at(),
            }
            for ticker, value in sorted(snapshot.items())
        ]
    )
    path = market_caps_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def fetch_market_caps(
    tickers: list[str],
    workers: int = 10,
    progress_cb: callable | None = None,
) -> dict[str, float]:
    """Fetch current market caps from Yahoo Finance, concurrently.

    Args:
        tickers: Ticker symbols to look up.
        workers: Number of concurrent yfinance requests.
        progress_cb: Optional ``(done, total)`` callback fired per completion.

    Returns:
        Dict mapping ticker -> market cap for every successful lookup.
    """
    import yfinance as yf

    total = len(tickers)
    done = 0
    lock = threading.Lock()
    results: dict[str, float] = {}

    def fetch_one(ticker: str) -> tuple[str, float | None]:
        try:
            value = yf.Ticker(ticker).fast_info.market_cap
            return ticker, None if value is None else float(value)
        except Exception:
            return ticker, None

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_one, t): t for t in tickers}
        for future in as_completed(futures):
            ticker, value = future.result()
            with lock:
                done += 1
                if value is not None and value > 0:
                    results[ticker] = value
                current, total_done = done, total
            if progress_cb is not None:
                progress_cb(current, total_done)

    return results


def refresh_market_caps(
    tickers: list[str] | None = None,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    workers: int = 10,
    progress_cb: callable | None = None,
    keep_missing: bool = True,
) -> pd.DataFrame | None:
    """Fetch market caps for ``tickers`` and update the on-disk cache.

    A fresh fetch is merged over the previous snapshot so tickers whose lookup
    failed keep their last known value (``keep_missing=True``, the default).

    Returns the updated :func:`load_market_caps` table or None on total failure.
    """
    data_dir = Path(data_dir)
    if tickers is None:
        tickers = current_member_tickers(data_dir)

    snapshot: dict[str, float] = {}
    if keep_missing:
        existing = load_market_caps(data_dir)
        if existing is not None:
            snapshot.update(dict(zip(existing["ticker"], existing["market_cap"])))

    fresh = fetch_market_caps(tickers, workers=workers, progress_cb=progress_cb)
    snapshot.update(fresh)

    if snapshot:
        save_market_caps(snapshot, data_dir)
    return load_market_caps(data_dir)


def market_cap_map(
    table: pd.DataFrame | None,
) -> dict[str, float]:
    """Ticker -> market cap dict from a :func:`load_market_caps` table."""
    if table is None or table.empty:
        return {}
    return dict(zip(table["ticker"], table["market_cap"]))


def format_market_cap(value: float | None) -> str:
    """Human-readable market cap, e.g. ``$4.91T``, ``$123.4B``, ``$567M``."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    v = float(value)
    if v >= 1e12:
        return f"${v / 1e12:,.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:,.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:,.0f}M"
    return f"${v:,.0f}"


def _fetched_at() -> str:
    return pd.Timestamp.today().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Per-company strategy drilldown
# ---------------------------------------------------------------------------


def analyze_company(
    ticker: str,
    start: str | None,
    end: str | None,
    universe: str = "current",
    data_dir: str | Path = DEFAULT_DATA_DIR,
    strategy_labels: list[str] | None = None,
) -> dict[str, dict]:
    """Run every registered strategy on a single company's price data.

    Args:
        ticker: Company ticker.
        start/end: Backtest window (YYYY-MM-DD strings or None).
        universe: "current" or "point_in_time" (uses S&P 500 membership so the
            ticker is restricted to the dates it was actually a member).
        data_dir: Directory containing the Parquet files.
        strategy_labels: Which strategies to run (default: all).

    Returns:
        Dict mapping strategy label -> the full ``run_strategy_analysis`` result
        (metrics, trades, equity_curve, signals, confidence).
    """
    specs = [
        spec
        for spec in STRATEGY_SPECS
        if strategy_labels is None or spec["label"] in strategy_labels
    ]

    feed_kwargs: dict = {
        "data_dir": data_dir,
        "start_date": start,
        "end_date": end,
    }
    if universe == "point_in_time":
        feed_kwargs["membership"] = str(DEFAULT_MEMBERSHIP_PATH)
        feed_kwargs["removed_data_dir"] = str(DEFAULT_REMOVED_DATA_DIR)

    results: dict[str, dict] = {}
    for spec in specs:
        label = spec["label"]
        feed_tickers = [ticker]
        if label == DUAL_MOMENTUM_LABEL and BENCHMARK_TICKER not in feed_tickers:
            feed_tickers.append(BENCHMARK_TICKER)
        try:
            feed = DataFeed(**feed_kwargs, tickers=feed_tickers)
            if len(feed) == 0:
                results[label] = _empty_result(label)
                continue
            engine = DataEngine(feed, spec["cls"](**spec["params"]))
            signals = engine.run()
            results[label] = run_strategy_analysis(
                signals,
                feed._data,
                label,
                end_date=pd.Timestamp(end) if end else None,
                membership=feed.membership,
            )
        except Exception:
            results[label] = _empty_result(label)
    return results


def company_summary_frame(results: dict[str, dict]) -> pd.DataFrame:
    """Per-strategy summary for one company, best total P&L first.

    Columns: strategy, total_trades, trades_completed, win_rate, total_pnl,
    avg_trade_pnl, cagr, sharpe, max_drawdown.
    """
    rows = []
    for label, result in results.items():
        m = result.get("metrics") or {}
        rows.append(
            {
                "strategy": label,
                "total_trades": m.get("total_trades", 0),
                "trades_completed": m.get("trades_completed", 0),
                "win_rate": m.get("win_rate", 0.0),
                "total_pnl": m.get("total_return", 0.0),
                "avg_trade_pnl": m.get("avg_trade_pnl", 0.0),
                "cagr": m.get("cagr", 0.0),
                "sharpe": m.get("sharpe", 0.0),
                "max_drawdown": m.get("max_drawdown", 0.0),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("total_pnl", ascending=False).reset_index(drop=True)
    return df


def recommend_strategy(summary: pd.DataFrame, min_trades: int | None = None) -> dict | None:
    """Pick which strategy a trader should follow for this company, and why.

    Considers only strategies with at least ``min_trades`` (default:
    MIN_TRADES_FOR_CONFIDENCE) completed trades so the verdict is based on a
    meaningful track record. Among those, each candidate is scored on four
    equally weighted dimensions — Sharpe ratio (clipped to [-3, 3]), win rate,
    CAGR (clipped to [-100%, 100%]), and drawdown magnitude (lower is better) —
    using min-max normalization across the qualified pool; total P&L breaks
    ties. This rewards strategies that are profitable with controlled risk.

    Args:
        summary: Per-strategy summary frame from :func:`company_summary_frame`.
        min_trades: Minimum completed trades for a reliable verdict. Defaults
            to MIN_TRADES_FOR_CONFIDENCE (3).

    Returns:
        A dict with the recommended strategy's label and the metrics behind the
        verdict, plus a list of human-readable reasons, or ``None`` when the
        summary is empty. If no strategy has enough completed trades, the one
        with the most trades is returned with ``low_confidence=True``.
    """
    if summary is None or summary.empty:
        return None

    min_trades = MIN_TRADES_FOR_CONFIDENCE if min_trades is None else min_trades

    def _pick(pool: pd.DataFrame) -> dict:
        def clamp(v: float, lo: float, hi: float) -> float:
            return max(lo, min(hi, float(v)))

        sharpe = pool["sharpe"].map(lambda v: clamp(v, -3.0, 3.0))
        cagr = pool["cagr"].map(lambda v: clamp(v, -1.0, 1.0))
        dd_mag = pool["max_drawdown"].abs()

        def _norm(series: pd.Series) -> pd.Series:
            lo, hi = float(series.min()), float(series.max())
            if hi - lo < 1e-12:
                return pd.Series(0.5, index=series.index)
            return (series - lo) / (hi - lo)

        score = (
            0.25 * _norm(sharpe)
            + 0.25 * _norm(pool["win_rate"])
            + 0.25 * _norm(cagr)
            + 0.25 * _norm(-dd_mag)  # lower drawdown -> higher score
        )
        scored = pool.assign(score=score).sort_values(
            ["score", "total_pnl"], ascending=[False, False]
        )
        best = scored.iloc[0]
        return {
            "strategy": str(best["strategy"]),
            "score": float(best["score"]),
            "trades_completed": int(best["trades_completed"]),
            "win_rate": float(best["win_rate"]),
            "total_pnl": float(best["total_pnl"]),
            "cagr": float(best["cagr"]),
            "sharpe": float(best["sharpe"]),
            "max_drawdown": float(best["max_drawdown"]),
        }

    qualified = summary[summary["trades_completed"] >= min_trades]
    if not qualified.empty:
        rec = _pick(qualified)
        rec["low_confidence"] = False
    else:
        max_trades = int(summary["trades_completed"].max())
        pool = summary[summary["trades_completed"] == max_trades]
        rec = _pick(pool)
        rec["low_confidence"] = True

    pnl = rec["total_pnl"]
    if pnl > 0:
        pnl_word = "the most profitable"
    elif pnl < 0:
        pnl_word = "the least-bad"
    else:
        pnl_word = "flat"
    rec["pnl_word"] = pnl_word

    reasons = [
        f"{rec['strategy']} blends the highest risk-adjusted return (Sharpe {rec['sharpe']:.2f}) "
        f"with a {rec['win_rate'] * 100:.0f}% win rate",
        f"annualized return of {rec['cagr'] * 100:+.1f}% and worst drawdown of "
        f"{rec['max_drawdown'] * 100:.1f}%",
        f"backed by {rec['trades_completed']} completed trades over the window",
    ]
    if rec["low_confidence"]:
        reasons.append(
            f"caution: no strategy reached {min_trades} completed trades, so this "
            "verdict is exploratory, not statistically grounded"
        )
    rec["reasons"] = reasons
    return rec


def trades_to_frame(trades: list) -> pd.DataFrame:
    """Individual completed trades for one strategy, newest first.

    Columns: Entry Date, Entry Price, Exit Date, Exit Price, P&L ($), P&L (%),
    Holding (days), Exit Reason. Trades still open at the end of the data
    (``exit_reason == "data_end"``) are excluded — they are counted separately
    in the metrics as "trades_open".
    """
    completed = [t for t in trades if t.exit_reason != "data_end"]
    return pd.DataFrame(
        [
            {
                "Entry Date": pd.Timestamp(t.entry_date).strftime("%Y-%m-%d"),
                "Entry Price": round(t.entry_price, 2),
                "Exit Date": pd.Timestamp(t.exit_date).strftime("%Y-%m-%d"),
                "Exit Price": round(t.exit_price, 2),
                "P&L ($)": round(t.pnl, 2),
                "P&L (%)": round(t.pnl_pct * 100, 2),
                "Holding (days)": int(t.holding_days),
                "Exit Reason": t.exit_reason,
            }
            for t in completed
        ]
    )


def _empty_result(label: str) -> dict:
    from thermaltrend.analytics.metrics import _empty_metrics

    metrics = _empty_metrics(0, 0)
    metrics["total_return"] = 0.0
    return {
        "strategy_name": label,
        "trades": [],
        "signals": [],
        "equity_curve": pd.Series([100_000.0]),
        "per_ticker": {},
        "metrics": metrics,
        "confidence": 0.0,
    }


def main() -> None:
    """CLI: ``python -m thermaltrend.company_universe --refresh``."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Pre-fetch and cache the S&P 500 market cap snapshot."
    )
    parser.add_argument(
        "--refresh", action="store_true", help="Fetch market caps for all members"
    )
    parser.add_argument(
        "--workers", type=int, default=10, help="Concurrent fetches (default 10)"
    )
    parser.add_argument(
        "--data-dir", default=str(DEFAULT_DATA_DIR), help="Directory with Parquet data"
    )
    args = parser.parse_args()

    if not args.refresh:
        table = load_market_caps(args.data_dir)
        if table is None:
            print("No market cap cache. Run with --refresh to build one.")
            sys.exit(1)
        print(table.to_string(index=False))
        return

    tickers = current_member_tickers(args.data_dir)
    print(f"Fetching market caps for {len(tickers)} tickers...")

    def progress(done: int, total: int) -> None:
        print(f"\r  {done}/{total}", end="", flush=True)

    table = refresh_market_caps(
        tickers=tickers,
        data_dir=args.data_dir,
        workers=args.workers,
        progress_cb=progress,
    )
    print()
    if table is None or table.empty:
        print("Fetch failed — check your network connection and retry.")
        sys.exit(1)
    print(f"Cached market caps for {len(table)} companies at {market_caps_path(args.data_dir)}")
    print(table.head(20).to_string(index=False))


if __name__ == "__main__":
    main()