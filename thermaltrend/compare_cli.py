"""
Compare multiple strategies across tickers and produce a ranking table.

Usage:
    python thermaltrend/compare_cli.py --tickers AAPL MSFT GOOGL --start 2023-01-01
    python thermaltrend/compare_cli.py --strategies ma_crossover donchian --tickers AAPL MSFT
    python thermaltrend/compare_cli.py --tickers AAPL MSFT --sort-by sharpe --output ranking.csv
"""

import json
from pathlib import Path

import pandas as pd

from thermaltrend.analytics.compare import compare_strategies, run_strategy_analysis
from thermaltrend.analytics.metrics import compute_benchmark_metrics
from thermaltrend.analytics.report import format_ranking_table
from thermaltrend.core.engine import DataEngine
from thermaltrend.core.strategy import (
    ATRTrailingStopStrategy,
    DonchianBreakoutStrategy,
    MACrossoverStrategy,
    RSIMeanReversionStrategy,
)
from thermaltrend.feed import DataFeed

DEFAULT_DATA_DIR = str(Path(__file__).parent / "data" / "equities")

STRATEGY_REGISTRY = {
    "ma_crossover": {"cls": MACrossoverStrategy, "label": "MA 50/200", "params": {}},
    "donchian": {"cls": DonchianBreakoutStrategy, "label": "Donchian 20/10", "params": {}},
    "rsi_mean_reversion": {"cls": RSIMeanReversionStrategy, "label": "RSI 14", "params": {}},
    "atr_trailing_stop": {"cls": ATRTrailingStopStrategy, "label": "ATR Trail 20/14/3", "params": {}},
}


def run_compare(
    tickers: list[str],
    strategy_names: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    sort_by: str = "sharpe",
    data_dir: str = DEFAULT_DATA_DIR,
) -> pd.DataFrame:
    """Run multi-strategy comparison. Library-friendly.

    Returns a ranking DataFrame with one row per strategy.
    """
    if strategy_names is None:
        strategy_names = list(STRATEGY_REGISTRY.keys())

    feed = DataFeed(data_dir, tickers=tickers, start_date=start_date, end_date=end_date)
    if len(feed) == 0:
        raise ValueError("No data found. Check your --tickers and date range.")

    results = {}
    for name in strategy_names:
        if name not in STRATEGY_REGISTRY:
            raise ValueError(
                f"Unknown strategy '{name}'. "
                f"Available: {', '.join(STRATEGY_REGISTRY.keys())}"
            )
        reg = STRATEGY_REGISTRY[name]
        strategy = reg["cls"](**reg["params"])
        label = reg["label"]

        engine = DataEngine(feed, strategy)
        signals = engine.run()
        result = run_strategy_analysis(signals, feed._data, label)
        results[label] = {"trades": result["trades"], "equity_curve": result["equity_curve"]}

    spy = pd.read_parquet(Path(__file__).parent / "data" / "equities" / "SPY.parquet")
    bench = compute_benchmark_metrics(spy, start_date or "1970-01-01", end_date or "2026-12-31")

    ranking = compare_strategies(results, benchmark_metrics=bench, sort_by=sort_by)
    return ranking


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Compare multiple strategies")
    parser.add_argument("--ticker", default=None, help="Single ticker (shorthand)")
    parser.add_argument("--tickers", nargs="+", default=None, help="Multiple tickers")
    parser.add_argument(
        "--strategies", nargs="+", default=None,
        choices=list(STRATEGY_REGISTRY.keys()),
        help="Strategies to compare (default: all)",
    )
    parser.add_argument("--start", default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--sort-by", default="sharpe",
        choices=["cagr", "sharpe", "sortino", "max_drawdown", "calmar", "win_rate", "total_trades"],
        help="Metric to sort by (default: sharpe)",
    )
    parser.add_argument("--output", default=None, help="Export ranking to CSV")
    parser.add_argument(
        "--data-dir", default=DEFAULT_DATA_DIR,
        help="Directory containing Parquet files",
    )
    args = parser.parse_args()

    tickers = args.tickers or ([args.ticker] if args.ticker else None)
    if not tickers:
        parser.error("Provide --ticker or --tickers")

    ranking = run_compare(
        tickers=tickers,
        strategy_names=args.strategies,
        start_date=args.start,
        end_date=args.end,
        sort_by=args.sort_by,
        data_dir=args.data_dir,
    )

    print(format_ranking_table(ranking))

    if args.output:
        out = Path(args.output)
        ranking.to_csv(out)
        print(f"Ranking exported to {out}")


if __name__ == "__main__":
    main()
