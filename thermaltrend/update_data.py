"""
Update existing stock data Parquet files with data up to today.

Reads each .parquet file, determines the last available date, downloads
incremental data from that point forward, and overwrites the file.

Also ensures SPY (S&P 500 ETF) data is always present and up to date.

Usage:
    python update_data.py                       # Update all tickers
    python update_data.py --tickers AAPL MSFT   # Update specific tickers
"""

import argparse
import gc
import time
from datetime import timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).parent / "data" / "equities"


def get_existing_tickers(data_dir: Path, tickers: list[str] | None) -> list[str]:
    """Return list of tickers that have existing parquet files."""
    if tickers:
        existing = [t for t in tickers if (data_dir / f"{t}.parquet").exists()]
        missing = set(tickers) - set(existing)
        if missing:
            print(f"Warning: no parquet file found for: {', '.join(sorted(missing))}")
        return existing

    return sorted(f.stem for f in data_dir.glob("*.parquet"))


def ensure_spy(data_dir: Path) -> None:
    """Download SPY data if it doesn't already exist."""
    spy_path = data_dir / "SPY.parquet"
    if spy_path.exists():
        return

    print("SPY.parquet not found - downloading initial SPY data...")
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        data = yf.download("SPY", start="1970-01-01", auto_adjust=True, progress=False)
        if not data.empty:
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.droplevel("Ticker")
            data.to_parquet(spy_path)
            print(f"  SPY - saved ({len(data)} rows)")
        else:
            print("  SPY - no data returned")
    except Exception as e:
        print(f"  SPY - FAILED to download: {e}")


def update_ticker(ticker: str, data_dir: Path) -> str:
    """Download incremental data for one ticker and overwrite its parquet file.

    Returns a status string: 'updated', 'up_to_date', or 'failed'.
    """
    out_path = data_dir / f"{ticker}.parquet"

    try:
        existing = pd.read_parquet(out_path)
    except Exception as e:
        print(f"  {ticker} - FAILED to read existing file: {e}")
        return "failed"

    last_date = existing.index.max()
    today = pd.Timestamp.now().normalize()

    if last_date >= today:
        print(f"  {ticker} - already up to date (last: {last_date.date()})")
        return "up_to_date"

    start_date = (last_date + timedelta(days=1)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    try:
        new_data = yf.download(
            ticker, start=start_date, end=end_date, auto_adjust=True, progress=False
        )
        if new_data.empty:
            print(f"  {ticker} - no new data returned (last: {last_date.date()})")
            return "up_to_date"

        if isinstance(new_data.columns, pd.MultiIndex):
            new_data.columns = new_data.columns.droplevel("Ticker")

        combined = pd.concat([existing, new_data])
        combined = combined[~combined.index.duplicated(keep="last")]
        combined.sort_index(inplace=True)

        new_rows = len(combined) - len(existing)
        if new_rows == 0:
            print(f"  {ticker} - already up to date (last: {last_date.date()})")
            return "up_to_date"

        combined.to_parquet(out_path)
        print(f"  {ticker} - updated (+{new_rows} rows, now {len(combined)} total)")
        return "updated"

    except Exception as e:
        print(f"  {ticker} - FAILED to download: {e}")
        return "failed"
    finally:
        gc.collect()


def main():
    parser = argparse.ArgumentParser(description="Update stock data Parquet files")
    parser.add_argument(
        "--tickers", nargs="+", default=None,
        help="Specific tickers to update (default: all existing)",
    )
    parser.add_argument(
        "--output", default=str(DATA_DIR),
        help="Directory containing Parquet files",
    )
    args = parser.parse_args()

    data_dir = Path(args.output)
    if not data_dir.exists():
        print(f"Data directory not found: {data_dir}")
        return

    # Ensure SPY data exists (download if missing)
    ensure_spy(data_dir)

    tickers = get_existing_tickers(data_dir, args.tickers)
    if not tickers:
        print("No parquet files found to update.")
        return

    print(f"Updating {len(tickers)} tickers...")
    print(f"Data directory: {data_dir}\n")

    counts = {"updated": 0, "up_to_date": 0, "failed": 0}

    for i, ticker in enumerate(tickers, 1):
        print(f"[{i}/{len(tickers)}]", end="")
        status = update_ticker(ticker, data_dir)
        counts[status] += 1
        if i < len(tickers):
            time.sleep(0.25)

    print(f"\nDone. Updated: {counts['updated']}, Up to date: {counts['up_to_date']}, Failed: {counts['failed']}")


if __name__ == "__main__":
    main()
