"""
Signal output tool for daily manual trading workflow.

Usage:
    python thermaltrend/signals.py
    python thermaltrend/signals.py --strategy ma_crossover --min-strength 0.5
    python thermaltrend/signals.py --tickers AAPL MSFT --start 2025-01-01
"""

from pathlib import Path

from thermaltrend.core.engine import DataEngine
from thermaltrend.core.strategy import (
    DonchianBreakoutStrategy,
    MACrossoverStrategy,
    RSIMeanReversionStrategy,
)
from thermaltrend.feed import DataFeed

DEFAULT_DATA_DIR = str(Path(__file__).parent / "data" / "equities")

STRATEGIES = {
    "ma_crossover": lambda: MACrossoverStrategy(fast_period=50, slow_period=200),
    "donchian": lambda: DonchianBreakoutStrategy(entry_period=20, exit_period=10),
    "rsi_mean_reversion": lambda: RSIMeanReversionStrategy(
        period=14, oversold=30.0, overbought=70.0
    ),
}


def format_signals_table(signals, strategy_name):
    """Format a list of SignalEvents as a readable table."""
    if not signals:
        return "No signals found."

    lines = [
        f"Signals ({strategy_name})",
        f"{'Date':12s} {'Ticker':8s} {'Direction':10s} {'Strength':10s}",
        "-" * 50,
    ]
    for s in sorted(signals, key=lambda x: x.strength, reverse=True):
        lines.append(
            f"{s.timestamp.date()!s:12s} {s.ticker:8s} {s.direction.value:10s} "
            f"{s.strength:>10.4f}"
        )
    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate trading signals")
    parser.add_argument(
        "--strategy", default="ma_crossover", choices=list(STRATEGIES.keys()),
        help="Strategy to use (default: ma_crossover)",
    )
    parser.add_argument("--tickers", nargs="+", default=None, help="Filter to specific tickers")
    parser.add_argument("--start", default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--min-strength", type=float, default=0.0, help="Minimum signal strength (0.0-1.0)")
    parser.add_argument("--direction", default=None, choices=["BUY", "SELL", "HOLD"], help="Filter by direction")
    parser.add_argument(
        "--data-dir", default=DEFAULT_DATA_DIR,
        help="Directory containing Parquet files",
    )
    args = parser.parse_args()

    strategy = STRATEGIES[args.strategy]()
    feed = DataFeed(args.data_dir, tickers=args.tickers, start_date=args.start, end_date=args.end)

    if len(feed) == 0:
        print("No data found. Check your --tickers and --data-dir arguments.")
        return

    engine = DataEngine(feed, strategy)
    signals = engine.run()

    filtered = [
        s for s in signals
        if s.strength >= args.min_strength
        and (args.direction is None or s.direction.value == args.direction)
    ]

    print(f"Data: {len(feed)} bars, {len(feed.tickers)} tickers, {len(feed.dates)} dates")
    print(f"Strategy: {args.strategy}")
    print()
    print(format_signals_table(filtered, args.strategy))


if __name__ == "__main__":
    main()
