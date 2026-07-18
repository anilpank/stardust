"""
Download S&P 500 historical stock data and store locally as Parquet.

Usage:
    python download_data.py                     # Download all S&P 500 stocks
    python download_data.py --tickers AAPL MSFT  # Download specific tickers
    python download_data.py --start 2020-01-01   # Custom start date
"""

import argparse
import time
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

DATA_DIR = Path(__file__).parent / "data"


def get_sp500_tickers() -> list[str]:
    """Fetch current S&P 500 constituents from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    tables = pd.read_html(url)
    df = tables[0]
    tickers = df["Symbol"].str.replace(".", "-", regex=False).tolist()
    print(f"Fetched {len(tickers)} S&P 500 tickers from Wikipedia")
    return tickers


def download_and_save(
    tickers: list[str],
    start: str,
    end: str,
    output_dir: Path,
) -> None:
    """Download OHLCV data for each ticker and save as Parquet."""
    output_dir.mkdir(parents=True, exist_ok=True)

    for i, ticker in enumerate(tickers, 1):
        out_path = output_dir / f"{ticker}.parquet"

        # Skip if already downloaded
        if out_path.exists():
            print(f"[{i}/{len(tickers)}] {ticker} - already exists, skipping")
            continue

        try:
            data = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
            if data.empty:
                print(f"[{i}/{len(tickers)}] {ticker} - no data returned, skipping")
                continue

            # Flatten multi-level columns from yfinance
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.droplevel("Ticker")

            data.to_parquet(out_path)
            print(f"[{i}/{len(tickers)}] {ticker} - saved ({len(data)} rows)")

            # Be polite to Yahoo's servers
            time.sleep(0.25)

        except Exception as e:
            print(f"[{i}/{len(tickers)}] {ticker} - FAILED: {e}")


def main():
    parser = argparse.ArgumentParser(description="Download stock data to Parquet")
    parser.add_argument(
        "--tickers", nargs="+", default=None,
        help="Specific tickers to download (default: all S&P 500)",
    )
    parser.add_argument("--start", default="2015-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2026-01-01", help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--output", default=str(DATA_DIR / "equities"),
        help="Output directory for Parquet files",
    )
    args = parser.parse_args()

    tickers = args.tickers if args.tickers else get_sp500_tickers()
    output_dir = Path(args.output)

    print(f"Downloading {len(tickers)} tickers ({args.start} to {args.end})")
    print(f"Saving to: {output_dir}\n")

    download_and_save(tickers, args.start, args.end, output_dir)

    saved = len(list(output_dir.glob("*.parquet")))
    print(f"\nDone. {saved} Parquet files in {output_dir}")


if __name__ == "__main__":
    main()
