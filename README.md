# stardust
Trading strategy system

## Prerequisites

Python 3.12+ with the following packages:

```
pip install pandas numpy yfinance requests pyarrow pytest
```

## Running Scripts

### Download Stock Data

Downloads S&P 500 historical OHLCV data as Parquet files:

```bash
cd thermaltrend

# Download all S&P 500 stocks (default: 2015-01-01 to 2026-01-01)
python download_data.py

# Download specific tickers
python download_data.py --tickers AAPL MSFT GOOGL

# Custom date range
python download_data.py --start 2020-01-01 --end 2024-01-01

# Custom output directory
python download_data.py --output ./my_data
```

Data is saved to `thermaltrend/data/equities/` by default. Already-downloaded tickers are skipped automatically.

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

