"""
Download historical price data for former S&P 500 members (removed tickers).

The standard universe (data/equities/) only contains today's members.
This downloads price data for companies that were removed from the index,
so step 4 can restrict trading to stocks that were actually members on each
date (survivorship-bias fix).

Companies removed from the index but still publicly traded download fine
(e.g. AAL, ESRT). Genuinely delisted companies (bankruptcies, take-private)
return no data from Yahoo Finance and are recorded in the coverage report.

Saved to data/equities_removed/ so the daily update (which globs
data/equities/) is unaffected.

Usage:
    python download_removed.py                # download all removed members
    python --tickers AAL ESRT                 # download specific tickers
    python --tickers AAL --output ./my_data   # custom output directory
"""

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).parent / "data"
EQUITIES_DIR = DATA_DIR / "equities"
REMOVED_DIR = DATA_DIR / "equities_removed"

START = "1970-01-01"


def removed_tickers() -> list[str]:
    membership = pd.read_csv(EQUITIES_DIR / "membership.csv", parse_dates=["date_added"])
    removed = sorted(membership.loc[~membership["is_current"], "ticker"].unique())
    current = set(membership.loc[membership["is_current"], "ticker"])
    already_have = {p.stem for p in EQUITIES_DIR.glob("*.parquet")}
    return [t for t in removed if t not in current and t not in already_have]


def _download_one(ticker: str) -> tuple[str, str]:
    out_path = REMOVED_DIR / f"{ticker}.parquet"
    if out_path.exists():
        return ticker, "skipped-existing"
    try:
        data = yf.download(ticker, start=START, auto_adjust=True, progress=False)
        if data.empty:
            return ticker, "no-data"
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.droplevel("Ticker")
        data.to_parquet(out_path)
        return ticker, f"saved-{len(data)}"
    except Exception as e:
        return ticker, f"error-{type(e).__name__}"


def download_and_save(tickers: list[str], workers: int) -> dict[str, str]:
    REMOVED_DIR.mkdir(parents=True, exist_ok=True)
    statuses: dict[str, str] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_download_one, t): t for t in tickers}
        for future in as_completed(futures):
            ticker, status = future.result()
            statuses[ticker] = status
            done += 1
            if status != "no-data":
                print(f"  {ticker} - {status}", flush=True)
            if done % 50 == 0:
                print(f"[{done}/{len(tickers)}]", flush=True)
            time.sleep(0.1)
    return statuses


def main():
    parser = argparse.ArgumentParser(description="Download former S&P 500 member data")
    parser.add_argument(
        "--tickers", nargs="+", default=None, help="Specific removed tickers",
    )
    parser.add_argument(
        "--workers", type=int, default=8, help="Parallel download workers (default: 8)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output directory for removed-ticker parquets (default: data/equities_removed/)",
    )
    args = parser.parse_args()

    global REMOVED_DIR
    if args.output:
        REMOVED_DIR = Path(args.output)

    tickers = removed_tickers() if not args.tickers else args.tickers
    if not tickers:
        print("No removed tickers to download.")
        return

    print(f"Downloading {len(tickers)} removed members into {REMOVED_DIR} "
          f"({args.workers} workers)\n")
    statuses = download_and_save(tickers, args.workers)

    from collections import Counter
    summary = Counter(s.split("-")[0] for s in statuses.values())
    print(f"\nDone. {summary}")


if __name__ == "__main__":
    main()