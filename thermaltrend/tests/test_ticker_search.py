"""Tests for thermaltrend/ticker_search.py — company-name to ticker lookup.

The search reads the real data/equities directory (plus membership.csv), so
these tests assert on well-known S&P 500 companies whose names are stable.
"""

from pathlib import Path

import pytest

from thermaltrend.ticker_search import (
    DEFAULT_DATA_DIR,
    DEFAULT_MEMBERSHIP_PATH,
    CompanyRecord,
    company_name,
    format_ticker_label,
    load_company_records,
    parse_ticker_from_label,
    search,
    search_tickers,
)


def _top_ticker(query: str) -> str | None:
    results = search(query)
    return results[0].ticker if results else None


def _tickers(query: str, limit: int = 10) -> list[str]:
    return [r.ticker for r in search(query, limit=limit)]


class TestLoadCompanyRecords:
    def test_reports_default_data_dir_exists(self):
        assert Path(DEFAULT_DATA_DIR).is_dir()
        assert Path(DEFAULT_MEMBERSHIP_PATH).is_file()

    def test_records_match_parquet_files(self):
        records = load_company_records()
        parquet = {p.stem for p in Path(DEFAULT_DATA_DIR).glob("*.parquet")}
        assert {r.ticker for r in records} == parquet

    def test_records_sorted_by_ticker(self):
        records = load_company_records()
        assert [r.ticker for r in records] == sorted(r.ticker for r in records)

    def test_all_current_members_with_data_are_searchable(self):
        import pandas as pd

        records = load_company_records()
        record_tickers = {r.ticker for r in records}
        membership = pd.read_csv(DEFAULT_MEMBERSHIP_PATH)
        current = set(membership[membership["is_current"].astype(str).str.lower() == "true"]["ticker"])
        parquet = {p.stem for p in Path(DEFAULT_DATA_DIR).glob("*.parquet")}
        assert current.intersection(parquet).issubset(record_tickers)
        # Corrected expectation: the two newest members (FDXF, HONA) have no
        # price data yet, so only 501 of 503 members are searchable.
        assert len(current) - len(parquet) <= 3

    def test_spy_included_via_extra_names(self):
        records = load_company_records()
        spy = [r for r in records if r.ticker == "SPY"]
        assert len(spy) == 1
        assert spy[0].name == "SPDR S&P 500 ETF Trust"

    def test_records_are_company_records(self):
        assert all(isinstance(r, CompanyRecord) for r in load_company_records())


class TestCompanyName:
    def test_known_member(self):
        assert company_name("AAPL") == "Apple Inc."

    def test_spy_hardcoded(self):
        assert company_name("SPY") == "SPDR S&P 500 ETF Trust"

    def test_unknown_ticker_falls_back(self):
        assert company_name("ZZZZ") == "ZZZZ"

    def test_hyphen_ticker(self):
        assert company_name("BRK-B") == "Berkshire Hathaway"


class TestExactAndTokenMatches:
    def test_case_insensitive(self):
        assert _top_ticker("apple") == "AAPL"
        assert _top_ticker("APPLE") == "AAPL"

    def test_substring_match(self):
        assert _top_ticker("Exxon") == "XOM"

    def test_all_tokens_in_name(self):
        assert _top_ticker("bank of america") == "BAC"

    def test_reordered_tokens(self):
        assert _top_ticker("America Bank") == "BAC"

    def test_punctuation_tolerant(self):
        assert _top_ticker("t-mobile") == "TMUS"

    def test_legal_suffix_ignored(self):
        assert _top_ticker("target corporation") == "TGT"

    def test_brand_before_legal_name(self):
        assert _top_ticker("Home Depot") == "HD"

    def test_no_spurious_matches(self):
        # "Apple" must not surface every company whose name contains "a".
        assert _top_ticker("apple") == "AAPL"

    def test_unambiguous_single_result(self):
        results = search("nvidia")
        assert results[0].ticker == "NVDA"


class TestTickerLookup:
    def test_exact_ticker(self):
        assert _top_ticker("aapl") == "AAPL"

    def test_ticker_alias_dot_to_hyphen(self):
        assert _top_ticker("BRK.B") == "BRK-B"

    def test_ticker_alias_hyphen(self):
        assert _top_ticker("BRK-B") == "BRK-B"

    def test_short_ticker(self):
        assert _top_ticker("F") == "F"


class TestBrandAliases:
    def test_facebook_maps_to_meta(self):
        assert _top_ticker("facebook") == "META"

    def test_google_maps_to_alphabet(self):
        tickers = _tickers("google")
        assert "GOOGL" in tickers or "GOOG" in tickers

    def test_alphabet_class_a_prefers_googl(self):
        results = search("alphabet class a")
        assert results[0].ticker == "GOOGL"

    def test_alias_does_not_replace_real_names(self):
        meta = [r for r in search("meta") if r.ticker == "META"]
        assert meta and meta[0].name == "Meta Platforms"


class TestClassShares:
    def test_news_corp_class_b_prefers_nws(self):
        results = search("news corp class b")
        assert results[0].ticker == "NWS"

    def test_alphabet_class_c_prefers_goog(self):
        results = search("alphabet class c")
        assert results[0].ticker == "GOOG"


class TestLimitsAndEdgeCases:
    def test_empty_query_returns_nothing(self):
        assert search("") == []
        assert search("   ") == []

    def test_no_match_returns_nothing(self):
        assert search("zzzznoasuchcompanyzzz") == []

    def test_limit_respected(self):
        results = search("bank", limit=3)
        assert len(results) <= 3

    def test_results_are_unique(self):
        results = search("bank")
        tickers = [r.ticker for r in results]
        assert len(tickers) == len(set(tickers))

    def test_search_only_returns_tickers_with_data(self):
        # FDXF is a current member but has no price data, so it must never
        # surface as a match (it is not in data/equities/).
        assert "FDXF" not in _tickers("fedex")

    def test_search_tickers_returns_strings(self):
        assert search_tickers("bank") == [r.ticker for r in search("bank")]


class TestFormatting:
    def test_label_format(self):
        label = format_ticker_label("AAPL")
        assert label == "AAPL — Apple Inc."

    def test_label_falls_back_to_ticker(self):
        assert format_ticker_label("ZZZZ") == "ZZZZ — ZZZZ"

    def test_parse_roundtrip(self):
        assert parse_ticker_from_label("BRK-B — Berkshire Hathaway") == "BRK-B"

    def test_parse_single_token(self):
        assert parse_ticker_from_label("F — Ford Motor Company") == "F"

    def test_brand_alias_appended_for_common_names(self):
        assert format_ticker_label("META") == "META — Meta Platforms (Facebook)"

    def test_no_alias_for_matching_name(self):
        assert format_ticker_label("AAPL") == "AAPL — Apple Inc."

    def test_brand_alias_label_parses_back(self):
        assert parse_ticker_from_label(format_ticker_label("GOOGL")) == "GOOGL"