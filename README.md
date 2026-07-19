# stardust
Trading strategy system

## Prerequisites

Python 3.12+ with the following packages:

```
pip install pandas numpy yfinance requests pyarrow pytest pre-commit
```

## Project Structure

```
thermaltrend/
├── data/
│   └── equities/              # Parquet files for each S&P 500 ticker
│       ├── constituents.csv   # S&P 500 members with date added
│       ├── AAPL.parquet
│       ├── MSFT.parquet
│       └── ...
├── download_data.py           # Download OHLCV data from Yahoo Finance
├── update_data.py             # Incrementally update existing Parquet files
├── show_start_dates.py        # Show data availability per company
└── tests/
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

## Running Tests

Install test dependency:

```bash
pip install pytest
```

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

