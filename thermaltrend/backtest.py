"""
Backtest a single strategy on one or more tickers.

Usage:
    python thermaltrend/backtest.py --strategy ma_crossover --ticker AAPL --start 2023-01-01
    python thermaltrend/backtest.py --strategy donchian --tickers AAPL MSFT --start 2023-01-01
    python thermaltrend/backtest.py --strategy ma_crossover --ticker AAPL --params '{"fast_period": 20, "slow_period": 50}'
    python thermaltrend/backtest.py --strategy rsi_mean_reversion --ticker AAPL --start 2023-01-01 --output result.json
"""

import json
from pathlib import Path

import pandas as pd

from thermaltrend.analytics.compare import run_strategy_analysis
from thermaltrend.analytics.metrics import compute_benchmark_metrics
from thermaltrend.analytics.regime import classify_regime, compute_regime_metrics
from thermaltrend.analytics.report import (
    format_per_ticker_table,
    format_ranking_table,
    format_regime_table,
    export_json,
    export_trades_csv,
)
from thermaltrend.analytics.compare import compare_strategies
from thermaltrend.core.engine import DataEngine
from thermaltrend.core.strategy import (
    ATRTrailingStopStrategy,
    DonchianBreakoutStrategy,
    MACrossoverStrategy,
    RSIMeanReversionStrategy,
)
from thermaltrend.feed import DataFeed

DEFAULT_DATA_DIR = str(Path(__file__).parent / "data" / "equities")

STRATEGIES = {
    "ma_crossover": lambda params=None: MACrossoverStrategy(
        **(params or {"fast_period": 50, "slow_period": 200})
    ),
    "donchian": lambda params=None: DonchianBreakoutStrategy(
        **(params or {"entry_period": 20, "exit_period": 10})
    ),
    "rsi_mean_reversion": lambda params=None: RSIMeanReversionStrategy(
        **(params or {"period": 14, "oversold": 30.0, "overbought": 70.0})
    ),
    "atr_trailing_stop": lambda params=None: ATRTrailingStopStrategy(
        **(params or {"entry_period": 20, "atr_period": 14, "atr_multiple": 3.0})
    ),
}


def run_backtest(
    strategy_name: str,
    tickers: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    params: dict | None = None,
    data_dir: str = DEFAULT_DATA_DIR,
) -> dict:
    """Run a full backtest for a single strategy. Library-friendly.

    Returns dict with keys: strategy_name, trades, equity_curve, per_ticker,
    metrics, confidence.
    """
    if strategy_name not in STRATEGIES:
        raise ValueError(
            f"Unknown strategy '{strategy_name}'. "
            f"Available: {', '.join(STRATEGIES.keys())}"
        )

    strategy = STRATEGIES[strategy_name](params)
    feed = DataFeed(data_dir, tickers=tickers, start_date=start_date, end_date=end_date)

    if len(feed) == 0:
        raise ValueError("No data found. Check your --tickers and date range.")

    engine = DataEngine(feed, strategy)
    signals = engine.run()
    result = run_strategy_analysis(signals, feed._data, strategy_name)
    return result


def _print_summary(result: dict, show_per_ticker: bool, show_regime: bool) -> None:
    """Print backtest results to terminal."""
    m = result["metrics"]
    name = result["strategy_name"]

    print()
    print(f"Strategy: {name}")
    print(f"Trades: {m['total_trades']} total, {m['trades_completed']} completed, {m['trades_open']} open")
    print()
    print(f"  CAGR:           {m['cagr'] * 100:>8.1f}%")
    print(f"  Sharpe:         {m['sharpe']:>8.2f}")
    print(f"  Sortino:        {m['sortino']:>8.2f}")
    print(f"  Max Drawdown:   {m['max_drawdown'] * 100:>8.1f}%")
    print(f"  Calmar:         {m['calmar']:>8.2f}")
    print(f"  Win Rate:       {m['win_rate'] * 100:>8.1f}%")
    pf = m['profit_factor']
    print(f"  Profit Factor:  {'N/A' if pf is None else f'{pf:>8.2f}'}")
    print(f"  Avg Trade PnL:  ${m['avg_trade_pnl']:>9.2f}")
    print(f"  Avg Holding:    {m['avg_holding_days']:>8.1f} days")
    print(f"  Total Return:   ${m['total_return']:>9.2f}")
    print(f"  Confidence:     {result['confidence']:>8.4f}")
    print()

    if show_per_ticker and result.get("per_ticker"):
        print(format_per_ticker_table(result["per_ticker"], name))

    if show_regime:
        spy = pd.read_parquet(Path(__file__).parent / "data" / "equities" / "SPY.parquet")
        regimes = classify_regime(spy["Close"])
        regime_m = compute_regime_metrics(result["trades"], regimes)
        print(format_regime_table(regime_m, name))


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Backtest a single strategy")
    parser.add_argument(
        "--strategy", required=True, choices=list(STRATEGIES.keys()),
        help="Strategy to backtest",
    )
    parser.add_argument("--ticker", default=None, help="Single ticker (shorthand)")
    parser.add_argument("--tickers", nargs="+", default=None, help="Multiple tickers")
    parser.add_argument("--start", default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--params", default=None,
        help='Strategy parameters as JSON, e.g. \'{"fast_period": 20}\'',
    )
    parser.add_argument("--per-ticker", action="store_true", help="Show per-ticker breakdown")
    parser.add_argument("--regime", action="store_true", help="Show regime analysis")
    parser.add_argument("--output", default=None, help="Export results to file (.json or .csv)")
    parser.add_argument(
        "--data-dir", default=DEFAULT_DATA_DIR,
        help="Directory containing Parquet files",
    )
    args = parser.parse_args()

    tickers = args.tickers or ([args.ticker] if args.ticker else None)
    if not tickers:
        parser.error("Provide --ticker or --tickers")

    params = json.loads(args.params) if args.params else None

    result = run_backtest(
        strategy_name=args.strategy,
        tickers=tickers,
        start_date=args.start,
        end_date=args.end,
        params=params,
        data_dir=args.data_dir,
    )

    _print_summary(result, show_per_ticker=args.per_ticker, show_regime=args.regime)

    if args.output:
        out = Path(args.output)
        if out.suffix == ".csv":
            export_trades_csv(result["trades"], out)
            print(f"Trades exported to {out}")
        else:
            export_json(result, out)
            print(f"Results exported to {out}")


if __name__ == "__main__":
    main()
