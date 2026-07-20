# stardust

Event-driven backtesting system for trading strategies on S&P 500 equities. Test, validate, and select the best performing strategies across multiple classes (trend, momentum, mean reversion, factor-based).

## Prerequisites

Python 3.12+ with the following packages:

```
pip install pandas numpy yfinance requests pyarrow pytest pre-commit
```

## Project Structure

```
thermaltrend/
├── core/                          # Event engine
│   ├── events.py                  # MarketEvent, SignalEvent, EventQueue
│   ├── strategy.py                # Strategy ABC + MACrossover, Donchian, RSI, ATR Trailing Stop
│   └── engine.py                  # DataEngine (main event loop)
├── analytics/                     # Strategy evaluation & comparison
│   ├── trade_simulator.py         # SignalEvents → simulated trades (ATR stops, $10K sizing)
│   ├── metrics.py                 # CAGR, Sharpe, Sortino, MaxDD, Calmar, confidence
│   ├── regime.py                  # Market regime detection (BULL/BEAR/SIDEWAYS)
│   ├── compare.py                 # Multi-strategy ranking table with benchmark
│   └── report.py                  # Terminal table + JSON + CSV export
├── data/
│   └── equities/                  # Parquet files for each S&P 500 ticker + SPY
│       ├── constituents.csv       # S&P 500 members with date added
│       ├── AAPL.parquet
│       ├── MSFT.parquet
│       ├── SPY.parquet            # S&P 500 ETF (benchmark)
│       └── ...
├── download_data.py               # Download OHLCV data from Yahoo Finance
├── update_data.py                 # Incrementally update existing Parquet files
├── show_start_dates.py            # Show data availability per company
├── feed.py                        # Data feed: load Parquet files as chronological bars
├── signals.py                     # Generate trading signals from strategy
└── tests/
    ├── test_events.py             # EventQueue + event type tests
    ├── test_strategy.py           # Strategy tests (MA, Donchian, RSI, ATR Trailing Stop)
    ├── test_engine.py             # DataEngine integration tests
    ├── test_signals.py            # Signal output tests
    ├── test_trade_simulator.py    # Trade simulation tests
    ├── test_metrics.py            # Metric calculation tests
    ├── test_regime.py             # Regime detection tests
    ├── test_compare.py            # Strategy comparison tests
    ├── test_report.py             # Report formatting tests
    ├── test_feed.py
    ├── test_feed_integration.py
    ├── test_download_data.py
    ├── test_download_integration.py
    ├── test_update_data.py
    ├── test_update_integration.py
    ├── test_show_start_dates.py
    └── test_show_start_dates_integration.py
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

# Filter by minimum strength and direction
python -m thermaltrend.signals --min-strength 0.5 --direction BUY

# Custom date range
python -m thermaltrend.signals --start 2024-01-01 --end 2024-12-31
```

Available strategies: `ma_crossover`, `donchian`, `rsi_mean_reversion`, `atr_trailing_stop`

Library usage:

```python
from thermaltrend.core.engine import DataEngine
from thermaltrend.core.strategy import MACrossoverStrategy, DonchianBreakoutStrategy, RSIMeanReversionStrategy, ATRTrailingStopStrategy
from thermaltrend.feed import DataFeed

feed = DataFeed("thermaltrend/data/equities", tickers=["AAPL", "MSFT"])
strategy = MACrossoverStrategy(fast_period=50, slow_period=200)
engine = DataEngine(feed, strategy)

signals = engine.run()
for s in signals:
    print(f"{s.timestamp.date()} {s.ticker} {s.direction.value} {s.strength:.4f}")
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
3. **Strategy Engine** — Strategy ABC + 4 strategies: MA Crossover, Donchian Breakout, RSI Mean Reversion, ATR Trailing Stop
4. **Analytics & Reporting** — Trade simulation, metrics, regime analysis, strategy ranking (built)
5. **Execution Handler** — simulated fills + live broker bridge (planned)
6. **Portfolio & Risk** — position sizing, risk management (planned)

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
