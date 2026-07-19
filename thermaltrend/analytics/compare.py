"""Multi-strategy comparison and ranking.

Runs the analytics pipeline for multiple strategies and produces
a side-by-side ranking table with SPY buy-and-hold as baseline.
"""

import pandas as pd

from thermaltrend.analytics.trade_simulator import Trade, TradeSimulator
from thermaltrend.analytics.metrics import (
    compute_aggregate_metrics,
    compute_benchmark_metrics,
    compute_confidence,
    compute_equity_curve,
    compute_per_ticker_metrics,
)


def compare_strategies(
    strategy_results: dict[str, dict],
    benchmark_metrics: dict | None = None,
    sort_by: str = "sharpe",
) -> pd.DataFrame:
    """Compare multiple strategies and produce a ranking table.

    Args:
        strategy_results: Dict mapping strategy_name -> {
            "trades": list[Trade],
            "equity_curve": pd.Series (optional),
            "per_ticker": dict (optional),
        }
        benchmark_metrics: Optional dict of benchmark (SPY B&H) metrics.
        sort_by: Metric to sort by (default "sharpe").

    Returns:
        DataFrame with one row per strategy, sorted by sort_by metric.
    """
    rows = []
    for name, result in strategy_results.items():
        trades = result.get("trades", [])
        equity_curve = result.get("equity_curve", None)

        metrics = compute_aggregate_metrics(trades, equity_curve)
        confidence = compute_confidence(trades)

        row = {"strategy": name}
        row.update(metrics)
        row["confidence"] = confidence
        rows.append(row)

    if benchmark_metrics:
        bench_row = {"strategy": "S&P 500 B&H"}
        bench_row.update(benchmark_metrics)
        bench_row["confidence"] = None
        rows.append(bench_row)

    df = pd.DataFrame(rows)

    priority_cols = [
        "strategy", "cagr", "sharpe", "sortino", "max_drawdown", "calmar",
        "win_rate", "profit_factor", "total_trades", "confidence",
    ]
    cols = [c for c in priority_cols if c in df.columns]
    extra = [c for c in df.columns if c not in cols]
    df = df[cols + extra]

    if sort_by in df.columns:
        ascending = sort_by not in ("max_drawdown", "total_trades")
        df = df.sort_values(sort_by, ascending=ascending, na_position="last")

    df = df.reset_index(drop=True)
    df.index = df.index + 1
    df.index.name = "rank"

    return df


def run_strategy_analysis(
    signals: list,
    price_data: pd.DataFrame,
    strategy_name: str,
    simulator: TradeSimulator | None = None,
) -> dict:
    """Full analysis pipeline for a single strategy.

    Args:
        signals: List of SignalEvents from the engine.
        price_data: DataFrame with MultiIndex (date, ticker).
        strategy_name: Name for this strategy.
        simulator: Optional custom TradeSimulator.

    Returns:
        Dict with trades, equity_curve, per_ticker, metrics, confidence.
    """
    if simulator is None:
        simulator = TradeSimulator()

    trades = simulator.simulate(signals, price_data)

    if trades:
        first_entry = min(t.entry_date for t in trades)
        equity_curve = compute_equity_curve(trades, pd.Timestamp(first_entry))
    else:
        equity_curve = pd.Series([100_000.0])

    per_ticker = compute_per_ticker_metrics(trades)
    metrics = compute_aggregate_metrics(trades, equity_curve)
    confidence = compute_confidence(trades)

    return {
        "strategy_name": strategy_name,
        "trades": trades,
        "equity_curve": equity_curve,
        "per_ticker": per_ticker,
        "metrics": metrics,
        "confidence": confidence,
    }
