"""Backtest the full portfolio system: core-satellite, sleeves, risk rules.

Runs one strategy over the satellite universe (SPY always injected for the
core-timing lane and regime gate), then simulates the shared-cash account
and reports the measured loss envelope: worst 12 months, max drawdown,
exposure/breaker statistics, per-year returns, and the SPY benchmark.

Usage:
    python thermaltrend/portfolio_backtest.py --strategy ma_crossover --tickers AAPL MSFT --capital 10000
    python thermaltrend/portfolio_backtest.py --strategy donchian --tickers AAPL MSFT --start 2018-01-01 --config live_config.json
    python thermaltrend/portfolio_backtest.py --strategy factor_scoring --universe point_in_time --start 2007-01-01
"""

import json
from dataclasses import replace
from pathlib import Path

from thermaltrend.analytics.report import export_json, export_trades_csv
from thermaltrend.backtest import (
    DEFAULT_DATA_DIR,
    DEFAULT_MEMBERSHIP_PATH,
    DEFAULT_REMOVED_DATA_DIR,
    STRATEGIES,
    UNIVERSE_CHOICES,
)
from thermaltrend.core.engine import DataEngine
from thermaltrend.feed import DataFeed
from thermaltrend.portfolio.config import PortfolioConfig, load_config
from thermaltrend.portfolio.manager import PortfolioManager, PortfolioResult


def run_portfolio_backtest(
    strategy_name: str,
    tickers: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    params: dict | None = None,
    data_dir: str = DEFAULT_DATA_DIR,
    universe: str = "current",
    config: PortfolioConfig | None = None,
    capital: float | None = None,
) -> PortfolioResult:
    """Run the portfolio system over history. Library-friendly."""
    if strategy_name not in STRATEGIES:
        raise ValueError(
            f"Unknown strategy '{strategy_name}'. "
            f"Available: {', '.join(STRATEGIES.keys())}"
        )
    cfg = config or PortfolioConfig()
    if capital is not None:
        cfg = replace(cfg, capital=capital)

    feed_tickers = list(dict.fromkeys([*tickers, cfg.core_ticker]))
    feed_kwargs = dict(
        data_dir=data_dir,
        tickers=feed_tickers,
        start_date=start_date,
        end_date=end_date,
    )
    if universe == "point_in_time":
        feed_kwargs.update(
            membership=DEFAULT_MEMBERSHIP_PATH,
            removed_data_dir=DEFAULT_REMOVED_DATA_DIR,
        )
    feed = DataFeed(**feed_kwargs)
    if len(feed) == 0:
        raise ValueError("No data found. Check your --tickers and date range.")

    engine = DataEngine(feed, STRATEGIES[strategy_name](params))
    signals = engine.run()
    return PortfolioManager(cfg).run(signals, feed._data)


def _print_summary(result: PortfolioResult, strategy_name: str) -> None:
    cfg = result.config
    m = result.metrics
    bench = m.get("benchmark") or {}

    print()
    print(f"Portfolio system — strategy: {strategy_name}")
    print(
        f"Capital: ${cfg.capital:,.0f}  "
        f"(band {cfg.band_min:.0%}–{cfg.band_max:.0%}, "
        f"risk {cfg.risk_per_trade:.2%}/trade, "
        f"breaker −{cfg.breaker_pct:.0%}, "
        f"ratchet {cfg.ratchet_rate:.0%})"
    )
    print(
        f"Equity: ${m['equity_start']:,.0f} → ${m['equity_end']:,.0f}  "
        f"({m['total_return_pct'] * 100:+.1f}%)"
    )
    print()
    print(f"  CAGR:           {m['cagr'] * 100:>8.1f}%")
    print(f"  Sharpe:         {m['sharpe']:>8.2f}")
    print(f"  Max Drawdown:   {m['max_drawdown'] * 100:>8.1f}%")
    worst = m.get("worst_12m")
    print(f"  Worst 12M:      {'N/A' if worst is None else f'{worst * 100:>7.1f}%'}")
    print(f"  Calmar:         {m['calmar']:>8.2f}")
    print(f"  Win Rate:       {m['win_rate'] * 100:>8.1f}%")
    print(f"  Trades:         {m['trades_total']:>8d}  "
          f"(core {m['core_trades']}, open {m['trades_open']})")
    print()
    print(f"  Avg Exposure:   {m['avg_exposure'] * 100:>8.1f}%  "
          f"(max {m['max_exposure'] * 100:.1f}%, "
          f"violations {m['band_violations']})")
    print(f"  Breaker Days:   {m['breaker_days_pct'] * 100:>8.1f}%")
    print(f"  Banked:         ${m['banked_total']:>8,.0f}  "
          f"(principal target ${m['principal_end']:,.0f})")
    print()

    if bench:
        print(
            f"vs SPY: {bench['spy_total_return'] * 100:+.1f}% total "
            f"(CAGR {bench['spy_cagr'] * 100:.1f}%, "
            f"MaxDD {bench['spy_max_drawdown'] * 100:.1f}%) — "
            f"excess {bench['excess_total_return'] * 100:+.1f}%"
        )
        print()

    per_year = m.get("per_year") or {}
    if per_year:
        print("Per-year returns:")
        for year, ret in per_year.items():
            print(f"  {year}: {ret * 100:>+7.1f}%")
        print()


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Backtest the core-satellite portfolio system"
    )
    parser.add_argument(
        "--strategy",
        required=True,
        choices=list(STRATEGIES.keys()),
        help="Satellite strategy",
    )
    parser.add_argument("--ticker", default=None, help="Single ticker (shorthand)")
    parser.add_argument("--tickers", nargs="+", default=None, help="Multiple tickers")
    parser.add_argument("--start", default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--params", default=None,
        help='Strategy parameters as JSON, e.g. \'{"fast_period": 20}\'',
    )
    parser.add_argument("--capital", type=float, default=None, help="Override capital")
    parser.add_argument("--config", default=None, help="Path to portfolio config JSON")
    parser.add_argument(
        "--universe", default="current", choices=UNIVERSE_CHOICES,
        help="'point_in_time' restricts members to their actual S&P 500 "
             "membership windows (requires membership.csv)",
    )
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument(
        "--output", default=None,
        help="Export results to file (.json or .csv)",
    )
    args = parser.parse_args()

    tickers = args.tickers or ([args.ticker] if args.ticker else None)
    if not tickers:
        if args.universe == "point_in_time":
            import pandas as pd

            membership = pd.read_csv(DEFAULT_MEMBERSHIP_PATH)
            tickers = sorted(membership["ticker"].unique())
        else:
            parser.error("Provide --ticker or --tickers")

    params = json.loads(args.params) if args.params else None
    config = load_config(args.config)

    result = run_portfolio_backtest(
        strategy_name=args.strategy,
        tickers=tickers,
        start_date=args.start,
        end_date=args.end,
        params=params,
        data_dir=args.data_dir,
        universe=args.universe,
        config=config,
        capital=args.capital,
    )
    _print_summary(result, args.strategy)

    if args.output:
        out = Path(args.output)
        if out.suffix == ".csv":
            export_trades_csv(result.trades, out)
            print(f"Trades exported to {out}")
        else:
            export_json(
                {
                    "strategy_name": args.strategy,
                    "config": result.config.to_dict(),
                    "metrics": result.metrics,
                    "trades": result.trades,
                    "equity_curve": result.equity_curve,
                    "ratchet_events": result.ratchet_events,
                    "breaker_events": result.breaker_events,
                    "rejected": result.rejected,
                },
                out,
            )
            print(f"Results exported to {out}")


if __name__ == "__main__":
    main()
