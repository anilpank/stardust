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
│   ├── strategy.py                # Strategy ABC + MACrossoverStrategy
│   └── engine.py                  # DataEngine (main event loop)
├── data/
│   └── equities/                  # Parquet files for each S&P 500 ticker
│       ├── constituents.csv       # S&P 500 members with date added
│       ├── AAPL.parquet
│       ├── MSFT.parquet
│       └── ...
├── download_data.py               # Download OHLCV data from Yahoo Finance
├── update_data.py                 # Incrementally update existing Parquet files
├── show_start_dates.py            # Show data availability per company
├── feed.py                        # Data feed: load Parquet files as chronological bars
├── signals.py                     # Generate trading signals from strategy
└── tests/
    ├── test_events.py             # EventQueue + event type tests
    ├── test_strategy.py           # MACrossoverStrategy tests
    ├── test_engine.py             # DataEngine integration tests
    ├── test_signals.py            # Signal output tests
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
# All signals for specific tickers
python -m thermaltrend.signals --tickers AAPL MSFT

# Filter by minimum strength and direction
python -m thermaltrend.signals --min-strength 0.5 --direction BUY

# Custom date range
python -m thermaltrend.signals --start 2024-01-01 --end 2024-12-31
```

Library usage:

```python
from thermaltrend.core.engine import DataEngine
from thermaltrend.core.strategy import MACrossoverStrategy
from thermaltrend.feed import DataFeed

feed = DataFeed("thermaltrend/data/equities", tickers=["AAPL", "MSFT"])
strategy = MACrossoverStrategy(fast_period=50, slow_period=200)
engine = DataEngine(feed, strategy)

signals = engine.run()
for s in signals:
    print(f"{s.timestamp.date()} {s.ticker} {s.direction.value} {s.strength:.4f}")
```

## Architecture

Event-driven design with 6 layers:

1. **Data Layer** — download, update, inspect Parquet files + DataFeed
2. **Event Queue** — MarketEvent → SignalEvent flow with strict chronological ordering
3. **Strategy Engine** — Strategy ABC + MACrossoverStrategy (trend, momentum, mean reversion, factor strategies planned)
4. **Execution Handler** — simulated fills + live broker bridge (planned)
5. **Portfolio & Risk** — position sizing, risk management (planned)
6. **Analytics & Reporting** — Sharpe, drawdown, tearsheet, strategy ranking & selection (planned)

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

