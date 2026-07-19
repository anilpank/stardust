"""
Show the start date of stock data for each S&P 500 company.

Usage:
    python show_start_dates.py              # Show all companies
    python show_start_dates.py --sort start # Sort by start date (default)
    python show_start_dates.py --sort ticker# Sort by ticker
    python show_start_dates.py --csv out.csv # Export to CSV
"""

import argparse
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data" / "equities"


def main():
    parser = argparse.ArgumentParser(description="Show data start dates for S&P 500 companies")
    parser.add_argument(
        "--sort", choices=["start", "ticker", "rows"], default="start",
        help="Sort by start date, ticker, or row count (default: start)",
    )
    parser.add_argument("--csv", default=None, help="Export results to CSV file")
    args = parser.parse_args()

    parquet_files = sorted(DATA_DIR.glob("*.parquet"))

    results = []
    for f in parquet_files:
        ticker = f.stem
        try:
            df = pd.read_parquet(f)
            results.append({
                "ticker": ticker,
                "start_date": df.index.min().date(),
                "end_date": df.index.max().date(),
                "rows": len(df),
            })
        except Exception as e:
            results.append({
                "ticker": ticker,
                "start_date": "ERROR",
                "end_date": str(e),
                "rows": 0,
            })

    df = pd.DataFrame(results)

    if df.empty:
        print("No parquet files found.")
        return

    errors = df[df["start_date"] == "ERROR"]
    valid = df[df["start_date"] != "ERROR"]

    sort_map = {"start": "start_date", "ticker": "ticker", "rows": "rows"}
    ascending = args.sort != "rows"

    if not valid.empty:
        valid = valid.sort_values(sort_map[args.sort], ascending=ascending).reset_index(drop=True)
    if not errors.empty:
        errors = errors.sort_values("ticker").reset_index(drop=True)

    df = pd.concat([valid, errors], ignore_index=True)

    print(f"{'TICKER':<10} {'START DATE':<15} {'END DATE':<15} {'ROWS':>6}")
    print("=" * 50)
    for _, row in df.iterrows():
        print(f"{row['ticker']:<10} {str(row['start_date']):<15} {str(row['end_date']):<15} {row['rows']:>6}")

    print(f"\nTotal companies: {len(df)}")
    if not valid.empty:
        print(f"Earliest data: {valid['start_date'].min()}")
        print(f"Latest start: {valid['start_date'].max()}")

    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"\nExported to {args.csv}")


if __name__ == "__main__":
    main()
