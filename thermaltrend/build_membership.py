"""
Build the historical S&P 500 membership table with removal dates.

Purpose: fix survivorship bias. The backtest universe is today's members;
this table records every company that was an S&P 500 member since 1996
(including those later removed), so strategies can restrict trading to
companies that were actually in the index on each date.

Sources:
- Historical membership (since 1996): fja05680/sp500 on GitHub
  (sp500_ticker_start_end.csv — derived from Wikipedia + S&P index changes).
- Authoritative add dates for current members: local constituents.csv
  (fetched from Wikipedia by download_data.py).

The date_added for current members comes from Wikipedia (true, back to 1957)
when the stock has been a member since the record start, or from the fja
stint start for re-added members (e.g. FISV re-added 2025-11-11). Historical
(removed) members only reconstruct from 1996; pre-1996 removals are not
covered by the free source.

Usage:
    python build_membership.py               # build membership.csv
    python build_membership.py --refresh     # re-download sources first
"""

import argparse
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).parent / "data"
EQUITIES_DIR = DATA_DIR / "equities"
SOURCE_DIR = DATA_DIR / "membership" / "sources"

RAW_BASE = "https://raw.githubusercontent.com/fja05680/sp500/master"
SOURCE_FILES = {
    "start_end": "sp500_ticker_start_end.csv",
    "names": "sp500.csv",
}

MEMBERSHIP_PATH = EQUITIES_DIR / "membership.csv"
HISTORY_CUTOFF = pd.Timestamp("1996-01-02")


def normalize_ticker(series: pd.Series) -> pd.Series:
    """yfinance uses hyphens (BRK-B); Wikipedia/fja use dots (BRK.B)."""
    return series.str.replace(".", "-", regex=False)


def ensure_sources(refresh: bool) -> None:
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    for key, filename in SOURCE_FILES.items():
        path = SOURCE_DIR / filename
        if path.exists() and not refresh:
            continue
        url = f"{RAW_BASE}/{filename}"
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        path.write_text(resp.text)
        print(f"Downloaded {filename} ({len(resp.text.splitlines())} lines)")


def load_start_end() -> pd.DataFrame:
    df = pd.read_csv(
        SOURCE_DIR / SOURCE_FILES["start_end"],
        parse_dates=["start_date", "end_date"],
    )
    df["ticker"] = normalize_ticker(df["ticker"])
    return df


def load_names() -> dict[str, str]:
    df = pd.read_csv(SOURCE_DIR / SOURCE_FILES["names"])
    df.columns = [c.strip() for c in df.columns]
    df["ticker"] = normalize_ticker(df["Symbol"].astype(str))
    return dict(zip(df["ticker"], df["Security"].astype(str)))


def build_membership() -> pd.DataFrame:
    start_end = load_start_end()
    names = load_names()
    constituents = pd.read_csv(EQUITIES_DIR / "constituents.csv", parse_dates=["date_added"])
    constituents["ticker"] = normalize_ticker(constituents["ticker"])
    add_dates = dict(zip(constituents["ticker"], constituents["date_added"]))

    rows = []
    for _, r in start_end.iterrows():
        ticker = r["ticker"]
        is_current = pd.isna(r["end_date"])
        if is_current:
            wiki_add = add_dates.get(ticker, r["start_date"])
            if r["start_date"] <= HISTORY_CUTOFF:
                date_added = wiki_add
            else:
                date_added = max(wiki_add, r["start_date"])
            date_removed = None
        else:
            date_added = r["start_date"]
            date_removed = r["end_date"]
        rows.append(
            {
                "ticker": ticker,
                "name": names.get(ticker, ""),
                "date_added": date_added,
                "date_removed": date_removed,
                "is_current": is_current,
            }
        )

    df = pd.DataFrame(rows)
    df = df.sort_values(["ticker", "date_added"]).reset_index(drop=True)
    return df


def validate_membership(df: pd.DataFrame, constituents: pd.DataFrame) -> list[str]:
    issues = []
    current = df[df["is_current"]]
    current_set = set(current["ticker"])
    const_set = set(normalize_ticker(constituents["ticker"]))

    if len(current) != len(constituents):
        issues.append(f"current count {len(current)} != constituents {len(constituents)}")
    if current_set != const_set:
        issues.append(
            f"current set mismatch: only in fja {current_set - const_set} "
            f"only in constituents {const_set - current_set}"
        )

    bad_dates = df[df["date_removed"].notna() & (df["date_removed"] <= df["date_added"])]
    if not bad_dates.empty:
        issues.append(f"rows with date_removed <= date_added: {len(bad_dates)}")

    overlaps = []
    for ticker, group in df.groupby("ticker"):
        added = group["date_added"].tolist()
        removed = group["date_removed"].tolist()
        for i in range(len(removed) - 1):
            if removed[i] is not None and added[i + 1] < removed[i]:
                overlaps.append(ticker)
    if overlaps:
        issues.append(f"overlapping stints for: {sorted(set(overlaps))[:10]}")

    return issues


def count_members_on(df: pd.DataFrame, date: pd.Timestamp) -> int:
    added = df["date_added"] <= date
    removed = df["date_removed"].isna() | (df["date_removed"] > date)
    return int((added & removed).sum())


def main():
    parser = argparse.ArgumentParser(description="Build historical S&P 500 membership table")
    parser.add_argument(
        "--refresh", action="store_true", help="Re-download source data from GitHub",
    )
    args = parser.parse_args()

    ensure_sources(args.refresh)
    df = build_membership()
    constituents = pd.read_csv(EQUITIES_DIR / "constituents.csv", parse_dates=["date_added"])

    issues = validate_membership(df, constituents)
    if issues:
        print("VALIDATION FAILURES:")
        for i in issues:
            print(f"  - {i}")
        raise SystemExit(1)

    df.to_csv(MEMBERSHIP_PATH, index=False)

    print(f"Built membership.csv ({len(df)} rows, {df['ticker'].nunique()} unique tickers)")
    print(f"Current members : {df['is_current'].sum()}")
    print(f"Removed stints  : {(~df['is_current']).sum()}")

    current_names = df[df["is_current"]]["name"]
    unnamed = (current_names == "").sum()
    if unnamed:
        print(f"  (current members missing names: {unnamed})")

    print("\nMembers on July 1 by year (should be ~500 from 1996; earlier years\n"
          "understate because pre-1996 removals are not in the free source):")
    counts = []
    for year in range(1957, 2027):
        counts.append((year, count_members_on(df, pd.Timestamp(f"{year}-07-01"))))
    print("  " + " ".join(f"{y}:{c}" for y, c in counts))

    by_decade = df[~df["is_current"]].copy()
    by_decade["end_year"] = by_decade["date_removed"].dt.year
    print("\nRemoved stints by end decade:")
    for year, group in by_decade.groupby((by_decade["end_year"] // 10) * 10):
        print(f"  {year}s: {len(group)}")

    print(f"\nSaved to {MEMBERSHIP_PATH}")


if __name__ == "__main__":
    main()