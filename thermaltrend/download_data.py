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


def get_sp500_constituents() -> pd.DataFrame:
    """Fetch current S&P 500 constituents with date added from Wikipedia.

    Returns DataFrame with columns: ticker, date_added
    """
    from io import StringIO

    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))
    df = tables[0]
    df["Symbol"] = df["Symbol"].str.replace(".", "-", regex=False)
    constituents = df[["Symbol", "Date added"]].rename(
        columns={"Symbol": "ticker", "Date added": "date_added"}
    )
    print(f"Fetched {len(constituents)} S&P 500 constituents from Wikipedia")
    return constituents


def get_sp500_tickers() -> list[str]:
    """Fetch current S&P 500 tickers from Wikipedia."""
    return get_sp500_constituents()["ticker"].tolist()


def get_universe(data_dir: Path, as_of_date: str) -> list[str]:
    """Return tickers that were S&P 500 members on as_of_date.

    Args:
        data_dir: Directory containing constituents.csv
        as_of_date: Date string (YYYY-MM-DD) to filter by
    """
    csv_path = data_dir / "constituents.csv"
    df = pd.read_csv(csv_path, parse_dates=["date_added"])
    mask = df["date_added"] <= pd.Timestamp(as_of_date)
    return df.loc[mask, "ticker"].tolist()


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
    parser.add_argument("--start", default="1970-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2026-01-01", help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--output", default=str(DATA_DIR / "equities"),
        help="Output directory for Parquet files",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)

    if args.tickers:
        tickers = args.tickers
        # When specific tickers are provided, skip constituents CSV
    else:
        constituents = get_sp500_constituents()
        tickers = constituents["ticker"].tolist()
        # Save constituents manifest for future universe filtering
        output_dir.mkdir(parents=True, exist_ok=True)
        constituents.to_csv(output_dir / "constituents.csv", index=False)
        print(f"Saved constituents.csv ({len(constituents)} tickers)")

    print(f"\nDownloading {len(tickers)} tickers ({args.start} to {args.end})")
    print(f"Saving to: {output_dir}\n")

    download_and_save(tickers, args.start, args.end, output_dir)

    saved = len(list(output_dir.glob("*.parquet")))
    print(f"\nDone. {saved} Parquet files in {output_dir}")


if __name__ == "__main__":
    main()
