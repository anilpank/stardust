# stardust

Event-driven backtesting system for trading strategies on S&P 500 equities. Test, validate, and select the best performing strategies across multiple classes (trend, momentum, mean reversion, factor-based).

## Prerequisites

Python 3.12+ with the following packages:

```
pip install pandas numpy yfinance requests pyarrow pytest pre-commit streamlit plotly
```

## Project Structure

```
thermaltrend/
├── core/                          # Event engine
│   ├── events.py                  # MarketEvent, SignalEvent, EventQueue
│   ├── strategy.py                # Strategy ABC + MA Crossover, Donchian, RSI,
│   │                              #   ATR Trailing Stop, Dual Momentum
│   └── engine.py                  # DataEngine (main event loop)
├── analytics/                     # Strategy evaluation & comparison
│   ├── trade_simulator.py         # SignalEvents → simulated trades (ATR stops, $10K sizing)
│   ├── metrics.py                 # CAGR, Sharpe, Sortino, MaxDD, Calmar, confidence
│   ├── regime.py                  # Market regime detection (BULL/BEAR/SIDEWAYS)
│   ├── compare.py                 # Multi-strategy ranking table with benchmark
│   └── report.py                  # Terminal table + JSON + CSV export
├── data/
│   ├── equities/                  # Parquet files for each S&P 500 ticker + SPY
│   │   ├── constituents.csv       # S&P 500 members with date added
│   │   ├── membership.csv         # Point-in-time membership stints (1957 → present)
│   │   ├── AAPL.parquet
│   │   └── ...
│   ├── equities_removed/          # Parquet files for removed S&P 500 members (survivorship-bias fix)
│   ├── signals/                   # Saved signal runs (one Parquet per run)
│   └── actions/                   # Action annotations (acted/skipped/pending)
├── download_data.py               # Download OHLCV data from Yahoo Finance
├── update_data.py                 # Incrementally update existing Parquet files
├── build_membership.py            # Build membership.csv (point-in-time membership stints)
├── download_removed.py            # Backfill price data for removed S&P 500 members
├── removed_coverage.py            # Per-stint data coverage report for removed members
├── survivorship_bias.py           # Quantify survivorship bias (--include-removed = PIT universe)
├── show_start_dates.py            # Show data availability per company
├── feed.py                        # Data feed: load Parquet files as chronological bars
├── backtest.py                    # Backtest a single strategy (CLI + library, --universe)
├── compare_cli.py                 # Compare multiple strategies (CLI + library, --universe)
├── signal_store.py                # Persist, query, and annotate signals
├── signals.py                     # Generate trading signals (with --save)
├── dashboard.py                   # Streamlit dashboard: backtests, signals, comparisons,
│                                  #   Data Explorer, Compare Tickers
├── charts.py                      # Plotly chart builders used by the dashboard
└── tests/                         # 426 tests across 26 files (394 fast unit + 32 integration)
```

## Running Scripts

### Download Stock Data

Downloads S&P 500 historical OHLCV data as Parquet files:

```bash
cd thermaltrend

# Download all S&P 500 stocks (default: 1970-01-01 to 2026-01-01)
python download_data.py

# Download specific tickers
python download_data.py --tickers AAPL MSFT GOOGL

# Custom date range
python download_data.py --start 2020-01-01 --end 2024-01-01

# Custom output directory
python download_data.py --output ./my_data
```

Data is saved to `thermaltrend/data/equities/` by default. Already-downloaded tickers are skipped automatically.

### Update Stock Data

Incrementally updates existing Parquet files with data up to today. Only downloads the missing days since the last available date — no full re-download needed:

```bash
cd thermaltrend

# Update all existing tickers
python update_data.py

# Update specific tickers only
python update_data.py --tickers AAPL MSFT GOOGL

# Custom data directory
python update_data.py --output ./my_data
```

Tickers that are already up to date are detected automatically (including weekends and holidays) and skipped without making network requests.

### Show Start Dates

Shows from which date each S&P 500 company has stock data available:

```bash
cd thermaltrend

# Show all companies (sorted by start date)
python show_start_dates.py

# Sort by ticker name
python show_start_dates.py --sort ticker

# Sort by most data (row count)
python show_start_dates.py --sort rows

# Export results to CSV
python show_start_dates.py --csv start_dates.csv
```

### Data Feed

Loads Parquet files and yields bars in strict chronological order for event-driven backtesting:

```python
from thermaltrend.feed import DataFeed

feed = DataFeed("thermaltrend/data/equities")

# Iterate all bars
for bar in feed:
    print(bar.ticker, bar.date, bar.close)

# Filter by tickers
feed = DataFeed("thermaltrend/data/equities", tickers=["AAPL", "MSFT"])

# Filter by date range
feed = DataFeed("thermaltrend/data/equities", start_date="2024-01-01", end_date="2024-12-31")

# All bars for a single date
bars = feed.get_bars_for_date("2024-01-02")

# Full history for one ticker (returns DataFrame)
df = feed.get_ticker_history("AAPL")
```

CLI usage:

```bash
cd thermaltrend

# Summary
python feed.py

# Filter tickers
python feed.py --tickers AAPL MSFT

# All bars for a date
python feed.py --date 2024-01-02

# Full history for a ticker
python feed.py --ticker-history AAPL

# First N bars
python feed.py --head 10
```

### Signal Generation

Runs a strategy on the data feed and outputs ranked trading signals:

```bash
# Default strategy (ma_crossover)
python -m thermaltrend.signals --tickers AAPL MSFT

# Use Donchian breakout strategy
python -m thermaltrend.signals --strategy donchian --tickers AAPL MSFT

# Use RSI mean reversion strategy
python -m thermaltrend.signals --strategy rsi_mean_reversion --tickers AAPL MSFT

# Use ATR trailing stop strategy (Chandelier Exit)
python -m thermaltrend.signals --strategy atr_trailing_stop --tickers AAPL MSFT

# Use dual momentum strategy (include the benchmark SPY in your tickers)
python -m thermaltrend.signals --strategy dual_momentum --tickers AAPL MSFT SPY

# Filter by minimum strength and direction
python -m thermaltrend.signals --min-strength 0.5 --direction BUY

# Custom date range
python -m thermaltrend.signals --start 2024-01-01 --end 2024-12-31
```

Available strategies: `ma_crossover`, `donchian`, `rsi_mean_reversion`, `atr_trailing_stop`, `dual_momentum`

Note: `dual_momentum` learns the benchmark series from the event stream — include `SPY` in your ticker list, otherwise it produces no signals.

Library usage:

```python
from thermaltrend.core.engine import DataEngine
from thermaltrend.core.strategy import (
    ATRTrailingStopStrategy,
    DonchianBreakoutStrategy,
    DualMomentumStrategy,
    MACrossoverStrategy,
    RSIMeanReversionStrategy,
)
from thermaltrend.feed import DataFeed

feed = DataFeed("thermaltrend/data/equities", tickers=["AAPL", "MSFT"])
strategy = MACrossoverStrategy(fast_period=50, slow_period=200)
engine = DataEngine(feed, strategy)

signals = engine.run()
for s in signals:
    print(f"{s.timestamp.date()} {s.ticker} {s.direction.value} {s.strength:.4f}")
```

Save signals to the store for later review:

```bash
python thermaltrend/signals.py --strategy ma_crossover --tickers AAPL MSFT --save
```

### Backtest

Run a single strategy and get full performance metrics:

```bash
# Basic backtest
python thermaltrend/backtest.py --strategy ma_crossover --ticker AAPL --start 2023-01-01

# Multiple tickers with per-ticker breakdown
python thermaltrend/backtest.py --strategy donchian --tickers AAPL MSFT GOOGL --per-ticker

# Custom parameters
python thermaltrend/backtest.py --strategy ma_crossover --ticker AAPL --params '{"fast_period": 20, "slow_period": 50}'

# Include regime analysis
python thermaltrend/backtest.py --strategy rsi_mean_reversion --ticker AAPL --regime

# Point-in-time universe (fixes survivorship bias — see section below)
python thermaltrend/backtest.py --strategy ma_crossover --universe point_in_time --start 2010-01-01

# Export results to JSON
python thermaltrend/backtest.py --strategy atr_trailing_stop --ticker AAPL --output result.json
```

Library usage:

```python
from thermaltrend.backtest import run_backtest

result = run_backtest("ma_crossover", ["AAPL", "MSFT"], start_date="2023-01-01")
print(result["metrics"])  # CAGR, Sharpe, Sortino, MaxDD, etc.
```

### Compare Strategies

Compare multiple strategies side-by-side with SPY benchmark:

```bash
# Compare all strategies
python thermaltrend/compare_cli.py --tickers AAPL MSFT GOOGL --start 2023-01-01

# Compare specific strategies, sorted by Sharpe
python thermaltrend/compare_cli.py --strategies ma_crossover donchian --ticker AAPL --sort-by sharpe

# Export ranking to CSV
python thermaltrend/compare_cli.py --tickers AAPL MSFT --output ranking.csv

# Point-in-time universe (fixes survivorship bias — see section below)
python thermaltrend/compare_cli.py --universe point_in_time --start 2010-01-01
```

Library usage:

```python
from thermaltrend.compare_cli import run_compare

ranking = run_compare(tickers=["AAPL", "MSFT", "GOOGL"], start_date="2023-01-01")
print(ranking)
```

### Point-in-Time Universe (Survivorship Bias)

Backtesting only today's S&P 500 members overstates returns: members that underperformed and were removed from the index are missing, even though they were tradable during the backtest window. `survivorship_bias.py` quantifies this at roughly **+2.6–4.3%/yr** on an equal-weight S&P portfolio versus the real equal-weight index.

The project fixes the bias two ways:

1. **`membership.csv`** — a historical member-stint table (1,259 stints across 1,206 tickers, 1957 → present). With `--universe point_in_time`, each ticker is only traded during its actual membership window.
2. **`data/equities_removed/`** — backfilled price data for 258 removed members (those still trading after leaving the index). When a member's price data runs out well before its removal date, the trade exits with a Shumway-style delisting return (−30% by default).

```bash
# Backtest with the point-in-time universe (default: current members only)
python thermaltrend/backtest.py --strategy ma_crossover --universe point_in_time --start 2010-01-01
python thermaltrend/compare_cli.py --universe point_in_time --start 2010-01-01

# Quantify the bias itself (survivors-only vs the real RSP equal-weight index)
python thermaltrend/survivorship_bias.py
# Bias-corrected estimate (adds removed-but-still-traded members)
python thermaltrend/survivorship_bias.py --include-removed
```

With the point-in-time universe the residual equal-weight bias drops to roughly **+0.9%/yr (2010)** and **+0.5%/yr (2015)**; the remaining 2020 residual (−0.4%/yr) is tied to cap-weight differences. About 60% of removed stints (456 of 756) have no price history at all — the delisting-return assumption covers those. The dashboard exposes the same switch via the **Universe** selector in the sidebar.

### Signal Store

Persist signals, query history, and annotate which ones you traded:

```bash
# List all saved signal runs
python thermaltrend/signal_store.py list

# Show signals from a specific run
python thermaltrend/signal_store.py show <run_id>

# Show signals from a date range
python thermaltrend/signal_store.py show --from 2026-07-01 --to 2026-07-20

# Annotate a signal (mark as acted or skipped)
python thermaltrend/signal_store.py annotate <signal_id> --action acted --notes "Bought at $195"

# Show pending signals (not yet acted on)
python thermaltrend/signal_store.py pending
```

### Analytics & Strategy Comparison

Evaluate strategy performance and compare multiple strategies:

```python
from thermaltrend.feed import DataFeed
from thermaltrend.core.engine import DataEngine
from thermaltrend.core.strategy import MACrossoverStrategy, DonchianBreakoutStrategy, RSIMeanReversionStrategy
from thermaltrend.analytics.compare import run_strategy_analysis, compare_strategies
from thermaltrend.analytics.metrics import compute_benchmark_metrics
from thermaltrend.analytics.report import format_ranking_table
from thermaltrend.core.strategy import MACrossoverStrategy, DonchianBreakoutStrategy, RSIMeanReversionStrategy, ATRTrailingStopStrategy
import pandas as pd

feed = DataFeed("thermaltrend/data/equities", tickers=["AAPL", "MSFT", "GOOGL"])

strategies = {
    "MA 50/200": MACrossoverStrategy(50, 200),
    "Donchian 20/10": DonchianBreakoutStrategy(20, 10),
    "RSI 14": RSIMeanReversionStrategy(14, 30.0, 70.0),
    "ATR Trailing Stop": ATRTrailingStopStrategy(20, 14, 3.0),
}

results = {}
for name, strat in strategies.items():
    engine = DataEngine(feed, strat)
    signals = engine.run()
    result = run_strategy_analysis(signals, feed._data, name)
    results[name] = {"trades": result["trades"], "equity_curve": result["equity_curve"]}

# Compare with SPY benchmark
spy = pd.read_parquet("thermaltrend/data/equities/SPY.parquet")
bench = compute_benchmark_metrics(spy, "2023-01-01", "2026-07-19")
ranking = compare_strategies(results, benchmark_metrics=bench)
print(format_ranking_table(ranking))
```

**Analytics features:**
- **Trade simulation**: Signals → trades with $10K sizing, 2× ATR stop loss, next-day open entry/exit
- **Aggregate metrics**: CAGR, Sharpe, Sortino, Max Drawdown, Calmar, win rate, profit factor
- **Per-ticker breakdown**: Which tickers the strategy performs best/worst on
- **Confidence score**: 0.0–1.0 composite score based on sample size, consistency, and ticker diversity
- **Market regime analysis**: Performance breakdown by BULL/BEAR/SIDEWAYS regimes
- **Multi-strategy ranking**: Side-by-side comparison table with SPY buy-and-hold baseline
- **Export**: Terminal table, JSON, CSV

## Architecture

Event-driven design with 6 layers:

1. **Data Layer** — download, update, inspect Parquet files + DataFeed
2. **Event Queue** — MarketEvent → SignalEvent flow with strict chronological ordering
3. **Strategy Engine** — Strategy ABC + 5 strategies: MA Crossover, Donchian Breakout, RSI Mean Reversion, ATR Trailing Stop, Dual Momentum
4. **Analytics & Reporting** — Trade simulation, metrics, regime analysis, strategy ranking, signal persistence
5. **Execution Handler** — simulated fills + live broker bridge (planned)
6. **Portfolio & Risk** — position sizing, risk management (planned)

## Dashboard

A Streamlit dashboard wraps the full workflow in a point-and-click interface:

```bash
streamlit run thermaltrend/dashboard.py
```

Tabs: Overview (metric cards, equity curve, drawdown, P&L distribution, price & signals), Trades, Per-Ticker, Regime, Signals, Compare, Saved Runs, plus standalone Data Explorer and Compare Tickers pages. Dual Momentum is fully supported — SPY is auto-added as its benchmark. A **Universe** selector in the sidebar switches between current-member and point-in-time universes. See `USER_GUIDE.md` Section 10 for a walkthrough.

## Running Tests

### Unit Tests (fast, no network)

```bash
pytest thermaltrend/tests/ -m "not slow" -v
```

### All Tests (includes network calls to Yahoo Finance)

```bash
pytest thermaltrend/tests/ -v
```

## Pre-commit Hooks

Pre-commit hooks run unit tests automatically on every commit. To set up:

```bash
pre-commit install
```

After installation, unit tests (excluding slow integration tests) run automatically before each commit. If any test fails, the commit is blocked until the issue is fixed.
