"""Tests for landing/generate_ticker_index.py.

The generator is pure: given a membership CSV it returns the ticker -> name
index payload. Tests use synthetic CSVs so they never touch the real data.
"""

from __future__ import annotations

import csv
from pathlib import Path

from landing import generate_ticker_index as gen


def _write_membership(tmp_path: Path, rows: list[list]) -> Path:
    path = tmp_path / "membership.csv"
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ticker", "name", "date_added", "date_removed", "is_current"])
        writer.writerows(rows)
    return path


class TestBuild:
    def test_only_current_members(self, tmp_path):
        path = _write_membership(tmp_path, [
            ["AAPL", "Apple Inc.", "1982-11-30", "", True],
            ["OLD", "Old Company", "1990-01-01", "2001-01-01", False],
        ])
        tickers = {e["ticker"]: e["name"] for e in gen.build(path)["tickers"]}
        assert tickers["AAPL"] == "Apple Inc."
        assert "OLD" not in tickers

    def test_includes_extras_and_brand_aliases(self, tmp_path):
        path = _write_membership(tmp_path, [["AAPL", "Apple Inc.", "1982-11-30", "", True]])
        entries = gen.build(path)["tickers"]
        by_ticker = {}
        for e in entries:
            by_ticker.setdefault(e["ticker"], []).append(e["name"])
        assert by_ticker["SPY"] == ["SPDR S&P 500 ETF Trust"]
        assert "Facebook" in by_ticker["META"]
        assert "Google" in by_ticker["GOOGL"]

    def test_sorted_with_metadata(self, tmp_path):
        path = _write_membership(tmp_path, [
            ["MSFT", "Microsoft", "", "", True],
            ["AAPL", "Apple Inc.", "", "", True],
        ])
        payload = gen.build(path)
        tickers = [e["ticker"] for e in payload["tickers"]]
        assert tickers == sorted(tickers)
        assert payload["generated_at"]

    def test_blank_name_falls_back_to_ticker(self, tmp_path):
        path = _write_membership(tmp_path, [["XYZ", "", "", "", True]])
        entries = {e["ticker"]: e["name"] for e in gen.build(path)["tickers"]}
        assert entries["XYZ"] == "XYZ"
