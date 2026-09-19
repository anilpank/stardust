"""Company-name to ticker lookup for the Thermaltrend dashboard.

Users often think of a company by its brand name ("Apple", "Bank of
America", "Berkshire Hathaway") rather than its ticker. This module turns a
free-text query (company name or partial ticker) into a ranked list of
matching tickers, entirely offline using the S&P 500 membership table and
the local Parquet file names — no network calls, no external fuzzy-match
dependency.

Matching is deliberately lightweight:

* case-insensitive and tolerant of punctuation ("t-mobile", "T-Mobile")
* legal-form suffixes are ignored ("Inc.", "Corporation", "Ltd.")
* exact/prefix/substring and all-token matches rank above partial hits
* ticker lookups (e.g. "aapl" or "BRK.B") resolve to the data ticker
* a small alias table covers well-known brand names that differ from the
  legal corporate name (e.g. "Facebook" -> META)

Used by the dashboard's ticker pickers so a user can search by company name
in the sidebar, Data Explorer, and Compare Tickers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEFAULT_DATA_DIR = Path(__file__).parent / "data" / "equities"
DEFAULT_MEMBERSHIP_PATH = DEFAULT_DATA_DIR / "membership.csv"

# Tickers in the data directory that are not S&P 500 members but should
# still be searchable. SPY is the Dual Momentum benchmark and is stored
# alongside the members under data/equities/.
EXTRA_NAMES = {
    "SPY": "SPDR S&P 500 ETF Trust",
}

# Alternate ticker spellings users are likely to type. Keys map to the
# canonical form used for the Parquet file names in the data directory.
TICKER_ALIASES = {
    "BRK.B": "BRK-B",
}

# Well-known brand names that differ from the legal company name. Keys are
# the normalized queries; values add extra normalized name tokens when
# matching (they never replace real names, so results are still accurate).
NAME_ALIASES = {
    "facebook": "meta platforms",
    "google": "alphabet",
}

# Brand names shown in dashboard labels so the native type-ahead autocomplete
# matches common names even when they differ from the legal company name.
DISPLAY_ALIASES = {
    "META": "Facebook",
    "GOOG": "Google",
    "GOOGL": "Google",
}

# Legal-form / filler tokens ignored when matching names. Kept intentionally
# small so substantive words ("Technologies", "Energy", "Financial") still
# participate in the match.
_STOP_TOKENS = {
    "an",
    "and",
    "co",
    "company",
    "corp",
    "corporation",
    "the",
    "group",
    "holding",
    "holdings",
    "inc",
    "incorporated",
    "limited",
    "llc",
    "ltd",
    "of",
    "plc",
    "sa",
}


@dataclass(frozen=True)
class CompanyRecord:
    """A searchable ticker with its human-readable company name."""

    ticker: str
    name: str


def _normalize(text: str) -> str:
    """Lowercase, drop non-alphanumerics, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _name_tokens(normalized: str) -> list[str]:
    return [t for t in normalized.split() if t not in _STOP_TOKENS]


def _canonical_ticker(query: str) -> str | None:
    """Map a query to a canonical data ticker if it is (or aliases) one."""
    q = re.sub(r"[^a-z0-9]", "", query, flags=re.IGNORECASE).upper()
    for alias, canonical in TICKER_ALIASES.items():
        if re.sub(r"[^a-z0-9]", "", alias, flags=re.IGNORECASE).upper() == q:
            return canonical
    return q


_records_cache: dict[tuple[str, str], list[CompanyRecord]] = {}


def load_company_records(
    data_dir: str | Path = DEFAULT_DATA_DIR,
    membership_path: str | Path = DEFAULT_MEMBERSHIP_PATH,
) -> list[CompanyRecord]:
    """Build searchable records for every Parquet file in ``data_dir``.

    Company names come from the ``is_current`` rows of ``membership.csv``;
    tickers without a name (or with a hardcoded extra, e.g. SPY) fall back
    to the ticker itself. Only tickers that actually have price data are
    returned, so every result is usable in the dashboard.
    """
    data_dir = Path(data_dir)
    membership_path = Path(membership_path)
    key = (str(data_dir), str(membership_path))
    if key in _records_cache:
        return _records_cache[key]

    names: dict[str, str] = {}
    if membership_path.exists():
        frame = pd.read_csv(membership_path)
        current = frame[frame["is_current"].astype(str).str.lower() == "true"]
        names.update(dict(zip(current["ticker"], current["name"].fillna(""))))

    records: list[CompanyRecord] = []
    for file in sorted(data_dir.glob("*.parquet")):
        ticker = file.stem
        name = names.get(ticker) or EXTRA_NAMES.get(ticker) or ticker
        records.append(CompanyRecord(ticker=ticker, name=name or ticker))
    for ticker, name in EXTRA_NAMES.items():
        if not any(r.ticker == ticker for r in records):
            records.append(CompanyRecord(ticker=ticker, name=name))

    records.sort(key=lambda r: r.ticker)
    _records_cache[key] = records
    return records


def _expand_query_tokens(raw_query: str) -> list[str]:
    """Normalized query tokens, augmented by any brand-name alias."""
    norm = _normalize(raw_query)
    tokens = [t for t in norm.split() if t not in _STOP_TOKENS]
    if norm in NAME_ALIASES:
        for extra in _normalize(NAME_ALIASES[norm]).split():
            if extra not in _STOP_TOKENS and extra not in tokens:
                tokens.append(extra)
    return tokens


def _ticker_rank(normalized_query: str) -> int:
    """Rank bonus for an exact ticker (or alias) match, else 0."""
    return 7 if _canonical_ticker(normalized_query) is not None else 0


def _name_rank(query_norm: str, query_tokens: list[str], record: CompanyRecord) -> int:
    name_norm = _normalize(record.name)
    name_tokens = _name_tokens(name_norm)
    if not query_tokens:
        return 0
    if query_norm == name_norm:
        return 6
    if all(t in name_tokens for t in query_tokens):
        return 5
    if query_norm in name_norm:
        return 4
    if name_norm in query_norm:
        return 3
    if any(t in name_norm for t in query_tokens if len(t) >= 3):
        return 1
    return 0


def search(
    query: str,
    *,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    membership_path: str | Path = DEFAULT_MEMBERSHIP_PATH,
    records: list[CompanyRecord] | None = None,
    limit: int = 10,
) -> list[CompanyRecord]:
    """Return up to ``limit`` records matching ``query``, best match first.

    Empty or whitespace-only queries return an empty list (the dashboard
    treats that as "show everything", not "no matches").
    """
    raw = query.strip()
    if not raw:
        return []
    records = records if records is not None else load_company_records(
        data_dir, membership_path
    )
    query_norm = _normalize(raw)
    query_tokens = _expand_query_tokens(raw)
    canonical = _canonical_ticker(raw)

    scored: list[tuple[int, int, CompanyRecord]] = []
    for record in records:
        rank = 0
        if canonical and canonical == record.ticker:
            rank = _ticker_rank(query_norm)
        name_rank = _name_rank(query_norm, query_tokens, record)
        if name_rank:
            rank = max(rank, name_rank)
        if rank:
            scored.append((rank, -len(_normalize(record.name)) if rank else 0, record))

    scored.sort(key=lambda t: (-t[0], -t[1], t[2].ticker))
    return [r for _, _, r in scored[:limit]]


def search_tickers(
    query: str,
    *,
    records: list[CompanyRecord] | None = None,
    limit: int = 10,
) -> list[str]:
    """Like :func:`search` but returns ticker symbols only."""
    return [r.ticker for r in search(query, records=records, limit=limit)]


def company_name(
    ticker: str,
    *,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    membership_path: str | Path = DEFAULT_MEMBERSHIP_PATH,
    records: list[CompanyRecord] | None = None,
) -> str:
    """Human-readable company name for ``ticker`` (falls back to the ticker)."""
    records = records if records is not None else load_company_records(
        data_dir, membership_path
    )
    for record in records:
        if record.ticker == ticker:
            return record.name
    return EXTRA_NAMES.get(ticker, ticker)


def format_ticker_label(ticker: str, *, records: list[CompanyRecord] | None = None) -> str:
    """Dashboard label, e.g. ``AAPL — Apple Inc.``.

    An extra brand alias is appended when it differs from the legal name
    (e.g. ``META — Meta Platforms (Facebook)``) so Streamlit's native
    type-ahead dropdown search matches the common name too.
    """
    name = company_name(ticker, records=records)
    alias = DISPLAY_ALIASES.get(ticker)
    if alias and alias.lower() not in name.lower():
        name = f"{name} ({alias})"
    return f"{ticker} — {name}"


def parse_ticker_from_label(label: str) -> str:
    """Inverse of :func:`format_ticker_label`."""
    return label.split(" — ", 1)[0].strip()


def main() -> None:
    """Little CLI for testing searches: ``python -m thermaltrend.ticker_search 'bank'``"""
    import argparse

    parser = argparse.ArgumentParser(description="Search S&P 500 tickers by company name.")
    parser.add_argument("query", help="Company name, brand, or ticker fragment to search for")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    for record in search(args.query, limit=args.limit):
        print(f"{record.ticker:<10} {record.name}")


if __name__ == "__main__":
    main()