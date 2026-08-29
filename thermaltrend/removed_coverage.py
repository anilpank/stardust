"""
Report price-data coverage for historical S&P 500 membership stints.

For every removed membership stint (see membership.csv), check whether price
data exists (in data/equities/ for re-added tickers, else data/equities_removed/)
and how much of the stint's membership window it covers.

This quantifies the residual survivorship gap: stints with no price data are
companies Facebook/CRSP would have recorded but that Yahoo no longer serves
(bankruptcies, take-privates) — most of the "missing losers" tail.

Usage:
    python removed_coverage.py
"""

from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
EQUITIES_DIR = DATA_DIR / "equities"
REMOVED_DIR = DATA_DIR / "equities_removed"

GRACE = pd.Timedelta(days=5)


def load_price(ticker: str) -> pd.DataFrame | None:
    for base in (EQUITIES_DIR, REMOVED_DIR):
        path = base / f"{ticker}.parquet"
        if path.exists():
            return pd.read_parquet(path)
    return None


def stint_status(start: pd.Timestamp, end: pd.Timestamp | None, ticker: str) -> str:
    data = load_price(ticker)
    if data is None:
        return "none"
    first, last = data.index.min(), data.index.max()
    covers_start = first <= start + GRACE
    covers_end = last >= end - GRACE if end is not None else last >= pd.Timestamp.now() - GRACE
    if covers_start and covers_end:
        return "full"
    if last < start + GRACE:
        return "none"
    return "partial"


def main():
    membership = pd.read_csv(EQUITIES_DIR / "membership.csv", parse_dates=["date_added", "date_removed"])
    removed = membership[~membership["is_current"]].copy()

    statuses = removed.apply(
        lambda r: stint_status(r["date_added"], r["date_removed"], r["ticker"]), axis=1
    )
    removed["coverage"] = statuses

    total = len(removed)
    counts = removed["coverage"].value_counts()
    print("REMOVED STINT COVERAGE (756 removed stints, 1996+)")
    print("=" * 50)
    for status in ["full", "partial", "none"]:
        n = counts.get(status, 0)
        print(f"  {status:8s} {n:4d}  ({n / total:5.1%})")

    print("\nBy removal decade:")
    removed["decade"] = (removed["date_removed"].dt.year // 10) * 10
    table = removed.pivot_table(
        index="decade", columns="coverage", values="ticker", aggfunc="count", fill_value=0
    )
    for col in ["full", "partial", "none"]:
        if col not in table.columns:
            table[col] = 0
    table = table[["full", "partial", "none"]]
    table["total"] = table.sum(axis=1)
    table["coverage%"] = (table["full"] + table["partial"]) / table["total"] * 100
    print(table.to_string())

    with_data = removed[removed["coverage"] != "none"]
    print(f"\nRemoved stints with at least partial price data: "
          f"{len(with_data)}/{total} ({len(with_data) / total:.1%})")

    print("\nNotable removed names with NO price data (sample):")
    none_tickers = removed.loc[removed["coverage"] == "none", "ticker"].unique()
    known = [t for t in ["ENRNQ", "LEHMQ", "WCOEQ", "MER", "AIGPQ", "JCP", "BBBY", "BLK", "DT", "KM"] if t in none_tickers]
    print("  " + ", ".join(known))


if __name__ == "__main__":
    main()