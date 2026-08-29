"""Unit tests for the survivorship-bias data tooling.

Covers build_membership, download_removed, removed_coverage, and
survivorship_bias — the pure-logic functions behind the point-in-time
universe. All tests are offline (paths are monkeypatched; network touched
only in test_download_removed_integration.py).
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from thermaltrend import build_membership as bm
from thermaltrend import download_removed as dr
from thermaltrend import removed_coverage as rc
from thermaltrend import survivorship_bias as sb


def _ohlcv(closes, dates):
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1 for c in closes],
            "Low": [c - 1 for c in closes],
            "Close": closes,
            "Volume": [100_000] * len(closes),
        },
        index=pd.DatetimeIndex(dates, name="date"),
    )


def _write(ticker, df, directory) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    df.to_parquet(directory / f"{ticker}.parquet")


# --------------------------------------------------------------------------
# build_membership
# --------------------------------------------------------------------------


class TestBuildMembership:
    def _write_sources(self, tmp_path):
        (tmp_path / "sp500_ticker_start_end.csv").write_text(
            "ticker,start_date,end_date\n"
            "BRK.B,2010-01-01,\n"
            "MMM,1957-03-01,\n"
            "ENRNQ,1998-01-05,2000-06-30\n"
        )
        (tmp_path / "sp500.csv").write_text(
            "Symbol,Security\n"
            "BRK.B,Berkshire Hathaway\n"
            "MMM,3M Company\n"
            "ENRNQ,Enron Corp\n"
        )

    def _write_constituents(self, tmp_path):
        (tmp_path / "constituents.csv").write_text(
            "ticker,date_added\nBRK-B,2010-02-16\nMMM,1957-03-04\n"
        )

    def test_normalize_ticker_replaces_dots(self):
        result = bm.normalize_ticker(pd.Series(["BRK.B", "BF.B", "BRK-B"]))
        assert list(result) == ["BRK-B", "BF-B", "BRK-B"]

    def test_build_membership_merges_sources(self, tmp_path, monkeypatch):
        self._write_sources(tmp_path)
        self._write_constituents(tmp_path)
        monkeypatch.setattr(bm, "SOURCE_DIR", tmp_path)
        monkeypatch.setattr(bm, "EQUITIES_DIR", tmp_path)

        df = bm.build_membership()

        assert list(df.columns) == ["ticker", "name", "date_added",
                                    "date_removed", "is_current"]
        # Current member added after the 1996 history cutoff: use the later
        # of the stint start and the Wikipedia (constituents) add date.
        brk = df[df["ticker"] == "BRK-B"].iloc[0]
        assert brk["is_current"] == True
        assert pd.isna(brk["date_removed"])
        assert pd.Timestamp(brk["date_added"]) == pd.Timestamp("2010-02-16")
        assert brk["name"] == "Berkshire Hathaway"
        # Current member added before the cutoff keeps the true Wikipedia date.
        mmm = df[df["ticker"] == "MMM"].iloc[0]
        assert mmm["is_current"] == True
        assert pd.Timestamp(mmm["date_added"]) == pd.Timestamp("1957-03-04")
        # Removed member uses the source start/end dates verbatim.
        enrnq = df[df["ticker"] == "ENRNQ"].iloc[0]
        assert enrnq["is_current"] == False
        assert pd.Timestamp(enrnq["date_added"]) == pd.Timestamp("1998-01-05")
        assert pd.Timestamp(enrnq["date_removed"]) == pd.Timestamp("2000-06-30")

    def _membership_df(self, rows):
        df = pd.DataFrame(
            rows,
            columns=["ticker", "name", "date_added", "date_removed", "is_current"],
        )
        df["date_added"] = pd.to_datetime(df["date_added"])
        df["date_removed"] = pd.to_datetime(df["date_removed"])
        return df

    def test_validate_membership_clean(self):
        df = self._membership_df([
            {"ticker": "AAPL", "name": "Apple", "date_added": "1982-11-30",
             "date_removed": None, "is_current": True},
            {"ticker": "ENRNQ", "name": "Enron", "date_added": "1998-01-05",
             "date_removed": "2000-06-30", "is_current": False},
        ])
        constituents = pd.DataFrame({"ticker": ["AAPL"], "date_added": ["1982-11-30"]})

        assert bm.validate_membership(df, constituents) == []

    def test_validate_membership_flags_current_mismatch(self):
        df = self._membership_df([
            {"ticker": "AAPL", "name": "Apple", "date_added": "1982-11-30",
             "date_removed": None, "is_current": True},
            {"ticker": "MSFT", "name": "MSFT", "date_added": "1976-03-31",
             "date_removed": None, "is_current": True},
        ])
        constituents = pd.DataFrame({"ticker": ["AAPL"], "date_added": ["1982-11-30"]})

        issues = bm.validate_membership(df, constituents)
        assert any("current count" in i for i in issues)
        assert any("set mismatch" in i for i in issues)

    def test_validate_membership_flags_bad_dates_and_overlap(self):
        df = self._membership_df([
            {"ticker": "AAPL", "name": "Apple", "date_added": "1982-11-30",
             "date_removed": None, "is_current": True},
            {"ticker": "NEWX", "name": "X", "date_added": "2020-01-01",
             "date_removed": "2019-01-01", "is_current": False},
            {"ticker": "REENT", "name": "R", "date_added": "2018-01-01",
             "date_removed": "2024-12-31", "is_current": False},
            {"ticker": "REENT", "name": "R", "date_added": "2024-06-01",
             "date_removed": None, "is_current": True},
        ])
        constituents = pd.DataFrame(
            {"ticker": ["AAPL", "REENT"], "date_added": ["1982-11-30", "2024-06-01"]}
        )

        issues = bm.validate_membership(df, constituents)
        assert any("date_removed <= date_added" in i for i in issues)
        assert any("overlapping stints" in i for i in issues)

    def test_count_members_on(self):
        df = self._membership_df([
            {"ticker": "MMM", "name": "3M", "date_added": "1957-03-04",
             "date_removed": None, "is_current": True},
            {"ticker": "ENRNQ", "name": "Enron", "date_added": "1995-01-01",
             "date_removed": "2000-06-30", "is_current": False},
        ])

        assert bm.count_members_on(df, pd.Timestamp("1998-07-01")) == 2
        assert bm.count_members_on(df, pd.Timestamp("2005-07-01")) == 1
        assert bm.count_members_on(df, pd.Timestamp("1990-07-01")) == 1


# --------------------------------------------------------------------------
# download_removed
# --------------------------------------------------------------------------


class TestRemovedTickers:
    def test_excludes_current_and_already_downloaded(self, tmp_path, monkeypatch):
        (tmp_path / "membership.csv").write_text(
            "ticker,name,date_added,date_removed,is_current\n"
            "AAPL,Apple,1982-11-30,,True\n"
            "AAL,American,1999-06-01,2015-03-23,False\n"
            "ENRNQ,Enron,1995-01-01,2000-06-30,False\n"
            "KM,Kmart,1995-01-01,2005-01-31,False\n"
        )
        _write("AAPL", _ohlcv([100.0] * 10, pd.date_range("2026-01-01", periods=10)), tmp_path)
        _write("AAL", _ohlcv([20.0] * 10, pd.date_range("2026-01-01", periods=10)), tmp_path)
        monkeypatch.setattr(dr, "EQUITIES_DIR", tmp_path)

        result = dr.removed_tickers()

        # AAPL is current; AAL already exists as a parquet; ENRNQ and KM are pending.
        assert result == ["ENRNQ", "KM"]


class TestDownloadAndSave:
    def test_saves_parquet_and_reports_status(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dr, "REMOVED_DIR", tmp_path)
        monkeypatch.setattr(dr.time, "sleep", lambda s: None)
        monkeypatch.setattr(
            dr.yf, "download",
            lambda *a, **k: _ohlcv([100 + i for i in range(5)],
                                   pd.date_range("2026-01-01", periods=5)),
        )

        statuses = dr.download_and_save(["AAL"], workers=1)

        assert (tmp_path / "AAL.parquet").exists()
        assert statuses["AAL"].startswith("saved-")

    def test_skips_existing_without_network_call(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dr, "REMOVED_DIR", tmp_path)
        (tmp_path / "AAL.parquet").touch()
        mock_yf = MagicMock()
        monkeypatch.setattr(dr, "yf", mock_yf)

        statuses = dr.download_and_save(["AAL"], workers=1)

        assert statuses["AAL"] == "skipped-existing"
        mock_yf.download.assert_not_called()

    def test_no_data_ticker_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dr, "REMOVED_DIR", tmp_path)
        monkeypatch.setattr(dr.time, "sleep", lambda s: None)
        monkeypatch.setattr(dr.yf, "download", lambda *a, **k: pd.DataFrame())

        statuses = dr.download_and_save(["ENRNQ"], workers=1)

        assert statuses["ENRNQ"] == "no-data"
        assert not (tmp_path / "ENRNQ.parquet").exists()

    def test_download_error_recorded_not_fatal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dr, "REMOVED_DIR", tmp_path)
        monkeypatch.setattr(dr.time, "sleep", lambda s: None)

        def boom(*a, **k):
            raise ConnectionError("network down")

        monkeypatch.setattr(dr.yf, "download", boom)

        statuses = dr.download_and_save(["FAIL"], workers=1)

        assert statuses["FAIL"].startswith("error-")
        assert not (tmp_path / "FAIL.parquet").exists()

    def test_flattens_multiindex_columns(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dr, "REMOVED_DIR", tmp_path)
        monkeypatch.setattr(dr.time, "sleep", lambda s: None)
        dates = pd.date_range("2026-01-01", periods=3)
        multi = pd.DataFrame(
            {("Close", "AAL"): [100, 101, 102], ("Volume", "AAL"): [1000, 1100, 1200]},
            index=dates,
        )
        multi.columns = pd.MultiIndex.from_tuples(
            [("Close", "AAL"), ("Volume", "AAL")], names=["Price", "Ticker"]
        )
        monkeypatch.setattr(dr.yf, "download", lambda *a, **k: multi)

        dr.download_and_save(["AAL"], workers=1)

        saved = pd.read_parquet(tmp_path / "AAL.parquet")
        assert list(saved.columns) == ["Close", "Volume"]


# --------------------------------------------------------------------------
# removed_coverage
# --------------------------------------------------------------------------


class TestStintStatus:
    @pytest.fixture(autouse=True)
    def _dirs(self, tmp_path, monkeypatch):
        equities = tmp_path / "equities"
        removed = tmp_path / "equities_removed"
        monkeypatch.setattr(rc, "EQUITIES_DIR", equities)
        monkeypatch.setattr(rc, "REMOVED_DIR", removed)
        return equities, removed

    def test_no_price_data_is_none(self):
        assert rc.stint_status(pd.Timestamp("2026-01-01"), pd.Timestamp("2026-02-01"), "X") == "none"

    def test_full_coverage(self, _dirs):
        _, removed = _dirs
        _write("X", _ohlcv([100] * 25, pd.bdate_range("2026-01-01", periods=25)), removed)

        assert rc.stint_status(pd.Timestamp("2026-01-01"), pd.Timestamp("2026-02-01"), "X") == "full"

    def test_partial_coverage(self, _dirs):
        _, removed = _dirs
        _write("X", _ohlcv([100] * 20, pd.bdate_range("2026-01-12", periods=20)), removed)

        assert rc.stint_status(pd.Timestamp("2026-01-01"), pd.Timestamp("2026-02-01"), "X") == "partial"

    def test_data_ends_before_stint_is_none(self, _dirs):
        _, removed = _dirs
        _write("X", _ohlcv([100] * 3, pd.bdate_range("2026-01-01", periods=3)), removed)

        assert rc.stint_status(pd.Timestamp("2026-01-01"), pd.Timestamp("2026-06-01"), "X") == "none"

    def test_current_stint_full_if_data_current(self, _dirs):
        _, removed = _dirs
        today = pd.Timestamp.today().normalize()
        dates = pd.bdate_range("2020-01-01", end=today)
        _write("X", _ohlcv([100.0] * len(dates), dates), removed)

        assert rc.stint_status(pd.Timestamp("2020-01-01"), None, "X") == "full"

    def test_current_stint_partial_when_overlapping_but_stale(self, _dirs):
        _, removed = _dirs
        # Data overlaps the membership start but ends well before today.
        _write("X", _ohlcv([100] * 20, pd.bdate_range("2026-01-05", periods=20)), removed)

        assert rc.stint_status(pd.Timestamp("2026-01-01"), None, "X") == "partial"

    def test_current_stint_stale_data_is_none(self, _dirs):
        _, removed = _dirs
        # Data ends entirely before the membership window opens.
        _write("X", _ohlcv([100] * 20, pd.bdate_range("2025-01-01", periods=20)), removed)

        assert rc.stint_status(pd.Timestamp("2026-01-01"), None, "X") == "none"

    def test_load_price_prefers_equities_dir(self, _dirs):
        equities, removed = _dirs
        _write("RS", _ohlcv([100] * 10, pd.date_range("2026-01-01", periods=10)), equities)
        _write("RS", _ohlcv([100] * 5, pd.date_range("2026-01-01", periods=5)), removed)

        data = rc.load_price("RS")

        assert len(data) == 10

    def test_load_price_returns_none_when_absent(self, _dirs):
        assert rc.load_price("GONE") is None


# --------------------------------------------------------------------------
# survivorship_bias
# --------------------------------------------------------------------------


MONTHS = pd.DatetimeIndex(
    ["2024-01-31", "2024-02-29", "2024-03-31", "2024-04-30",
     "2024-05-31", "2024-06-30", "2024-07-31", "2024-08-31",
     "2024-09-30", "2024-10-31", "2024-11-30", "2024-12-31", "2025-01-31"],
    name="date",
)


class TestEqualWeightSeries:
    def _monthly(self, closes):
        return pd.Series(closes, index=MONTHS)

    def _member_ship(self, rows):
        return pd.DataFrame(
            rows,
            columns=["ticker", "name", "date_added", "date_removed", "is_current"],
        )

    def test_plain_equal_weight_mean_of_monthly_returns(self):
        x = self._monthly([100, 110, 121, 121, 121, 121, 121, 121, 121, 121, 121, 121, 121])
        y = self._monthly([200, 210, 220, 220, 220, 220, 220, 220, 220, 220, 220, 220, 220])
        series = sb.equal_weight_series({"X": x, "Y": y}, MONTHS[0], MONTHS[-1])

        # First pct_change row (Feb) is dropped only when ALL columns are NaN;
        # here both X and Y are valid everywhere, so Feb = (10% + 5%) / 2.
        assert series.index[0] == pd.Timestamp("2024-02-29")
        assert series.iloc[0] == pytest.approx(0.075)
        assert series.iloc[1] == pytest.approx(0.07380952380952, abs=1e-12)

    def test_membership_masks_non_member_months(self):
        x = self._monthly([100, 110, 121, 121, 121, 121, 121, 121, 121, 121, 121, 121, 121])
        y = self._monthly([200, 210, 220, 220, 220, 220, 220, 220, 220, 220, 220, 220, 220])
        membership = self._member_ship([
            {"ticker": "X", "name": "X", "date_added": "2024-01-01",
             "date_removed": None, "is_current": True},
            {"ticker": "Y", "name": "Y", "date_added": "2024-03-01",
             "date_removed": None, "is_current": True},
        ])
        series = sb.equal_weight_series(
            {"X": x, "Y": y}, MONTHS[0], MONTHS[-1], membership=membership
        )

        # Y is not a member until March: its Feb close is masked, so both Feb
        # and Mar pct_change rows (Y's is NaN-poisoned from Feb) reflect only X.
        assert series.index[0] == pd.Timestamp("2024-02-29")
        assert series.iloc[0] == pytest.approx(0.10)
        assert series.iloc[1] == pytest.approx(0.10)


class TestCagr:
    def test_empty_series_is_nan(self):
        assert pd.isna(sb.cagr(pd.Series(dtype=float), pd.Timestamp("2025-01-31")))

    def test_zero_returns_yield_zero(self):
        series = pd.Series([0.0, 0.0, 0.0], index=MONTHS[:3])
        assert sb.cagr(series, MONTHS[2]) == pytest.approx(0.0, abs=1e-12)

    def test_negative_total_is_nan(self):
        series = pd.Series([-2.0], index=MONTHS[:1])
        assert pd.isna(sb.cagr(series, MONTHS[2]))

    def test_known_compounding(self):
        series = pd.Series([0.03] * 12, index=MONTHS[:12])
        # 12 months of +3% ≈ 42.6% annualized (window ≈ 365 days).
        assert sb.cagr(series, pd.Timestamp("2025-01-31")) == pytest.approx(0.426, abs=0.01)


class TestLoadMonthlyCloses:
    def test_resamples_daily_close_to_month_end(self, tmp_path, monkeypatch):
        equities = tmp_path / "equities"
        removed = tmp_path / "equities_removed"
        monkeypatch.setattr(sb, "EQUITIES_DIR", equities)
        monkeypatch.setattr(sb, "REMOVED_DIR", removed)
        _write("X", _ohlcv(list(range(40, 80)), pd.bdate_range("2025-01-02", periods=40)), equities)

        closes = sb.load_monthly_closes(["X"])

        assert "X" in closes
        monthly = closes["X"]
        assert monthly.index.is_month_end.all()
        assert monthly.iloc[-1] == 79  # last close of the final month in range

    def test_removed_dir_only_used_after_equities_miss(self, tmp_path, monkeypatch):
        equities = tmp_path / "equities"
        removed = tmp_path / "equities_removed"
        monkeypatch.setattr(sb, "EQUITIES_DIR", equities)
        monkeypatch.setattr(sb, "REMOVED_DIR", removed)
        _write("DEL", _ohlcv([10, 11, 12, 13, 14], pd.bdate_range("2025-01-03", periods=5)), removed)

        assert "DEL" not in sb.load_monthly_closes(["DEL"], include_removed=False)
        assert "DEL" in sb.load_monthly_closes(["DEL"], include_removed=True)


class TestFormatReport:
    def _result(self, expanded=None):
        return {
            "year": 2010,
            "members_added_by_start": 506,
            "members_with_data": 489,
            "buyhold_cagr": 0.10,
            "benchmarks": {
                "SPY": {
                    "survivors_cagr": 0.12,
                    "expanded_cagr": expanded,
                    "expanded_active": 512,
                    "index_cagr": 0.11,
                    "gap": 0.01,
                    "window_start": pd.Timestamp("2010-01-01"),
                },
            },
        }

    def test_renders_survivors_and_gap(self):
        text = sb.format_report([self._result()], None)

        assert "SURVIVORSHIP BIAS REPORT" in text
        assert "Start year 2010" in text
        assert "+1.00%/yr" in text

    def test_renders_expanded_line_when_available(self):
        text = sb.format_report([self._result(expanded=0.112)], None)

        assert "Expanded PIT universe, 512 members with data" in text
        assert "remaining gap to SPY: +0.20%/yr" in text

    def test_csv_mode_appends_per_ticker_table(self, tmp_path, monkeypatch):
        equities = tmp_path / "equities"
        monkeypatch.setattr(sb, "EQUITIES_DIR", equities)
        dates = pd.bdate_range("2010-01-04", periods=1400)
        _write("X", _ohlcv(list(range(1400)), dates), equities)
        equities.mkdir(parents=True, exist_ok=True)
        (equities / "constituents.csv").write_text("ticker,date_added\nX,2005-01-03\n")

        text = sb.format_report([self._result()], tmp_path / "out.csv")

        assert "Per-ticker survivors table (2010 onward)" in text
        assert "1 members; top 5:" in text
        assert (tmp_path / "out.csv").exists()


class TestAnalyzeYearOffline:
    def test_analyze_year_runs_fully_offline(self, monkeypatch):
        monthly = pd.date_range("2009-12-31", periods=200, freq="ME")

        def closes_for(base, drift):
            return pd.Series([base * (1 + drift) ** i for i in range(200)], index=monthly)

        def fake_constituents():
            return pd.DataFrame(
                {"ticker": ["AA", "BB"], "date_added": pd.to_datetime(["2005-01-01", "2008-01-01"])}
            )

        def fake_membership():
            return pd.DataFrame(
                {
                    "ticker": ["AA", "BB", "ENR"],
                    "name": ["aa", "bb", "enr"],
                    "date_added": pd.to_datetime(["2005-01-01", "2008-01-01", "2005-01-01"]),
                    "date_removed": pd.to_datetime([None, None, "2015-01-01"]),
                    "is_current": [True, True, False],
                }
            )

        closes = {"AA": closes_for(100, 0.01),
                  "BB": closes_for(200, 0.005),
                  "ENR": closes_for(50, -0.02)}

        def fake_closes(tickers, include_removed=False):
            return {t: closes[t] for t in tickers}

        def fake_benchmark(symbol):
            return closes_for({"SPY": 300, "RSP": 400}[symbol], 0.008)

        monkeypatch.setattr(sb, "load_constituents", fake_constituents)
        monkeypatch.setattr(sb, "load_membership", fake_membership)
        monkeypatch.setattr(sb, "load_monthly_closes", fake_closes)
        monkeypatch.setattr(sb, "fetch_benchmark", fake_benchmark)

        result = sb.analyze_year(2010, include_removed=True, membership=fake_membership())

        assert result["year"] == 2010
        assert result["members_added_by_start"] == 2
        assert set(result["benchmarks"]) == {"SPY", "RSP"}
        for b in result["benchmarks"].values():
            assert pd.notna(b["survivors_cagr"])
            assert pd.notna(b["gap"])
            assert b["expanded_cagr"] is not None
            assert b["expanded_active"] >= 2


class TestPerTickerTable:
    def test_returns_sorted_cagr_table(self, tmp_path, monkeypatch):
        equities = tmp_path / "equities"
        monkeypatch.setattr(sb, "EQUITIES_DIR", equities)
        dates = pd.bdate_range("2010-01-04", periods=1000)
        _write("SLOW", _ohlcv([100.0] * 1000, dates), equities)
        _write("FAST", _ohlcv([100 * 1.002**i for i in range(1000)], dates), equities)
        equities.mkdir(parents=True, exist_ok=True)
        (equities / "constituents.csv").write_text(
            "ticker,date_added\nSLOW,2005-01-03\nFAST,2005-01-03\n"
        )

        table = sb.per_ticker_table(pd.Timestamp("2010-01-01"))

        assert list(table.columns) == ["ticker", "total_return", "cagr"]
        assert len(table) == 2
        assert table["ticker"].tolist()[0] == "FAST"
        assert table["cagr"].is_monotonic_decreasing