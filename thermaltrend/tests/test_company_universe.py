"""Tests for thermaltrend.company_universe — market cap cache and per-company drilldown."""

from pathlib import Path

import pandas as pd
import pytest

from thermaltrend.company_universe import (
    DEFAULT_STRATEGY_LABELS,
    MIN_TRADES_FOR_CONFIDENCE,
    analyze_company,
    company_summary_frame,
    current_member_tickers,
    fetch_market_caps,
    format_market_cap,
    load_market_caps,
    market_cap_map,
    recommend_strategy,
    refresh_market_caps,
    save_market_caps,
    trades_to_frame,
)


def _make_parquet(tmp_path, ticker, dates_closes):
    df = pd.DataFrame(
        {
            "Open": [c for _, c in dates_closes],
            "High": [c + 1 for _, c in dates_closes],
            "Low": [c - 1 for _, c in dates_closes],
            "Close": [c for _, c in dates_closes],
            "Volume": [1000] * len(dates_closes),
            "ticker": ticker,
        },
        index=pd.DatetimeIndex([d for d, _ in dates_closes], name="Date"),
    )
    df.to_parquet(tmp_path / f"{ticker}.parquet")


def _membership_csv(tmp_path):
    (tmp_path / "membership.csv").write_text(
        "ticker,name,date_added,date_removed,is_current\n"
        "TEST,Test Corp,2020-01-01,,True\n"
        "SPY,SPDR S&P 500 ETF Trust,1993-01-29,,True\n"
        "GONE,Removed Co,2020-01-01,2022-06-01,False\n"
    )


class TestMarketCapCache:
    def test_load_returns_none_when_no_cache(self, tmp_path):
        assert load_market_caps(tmp_path) is None

    def test_save_then_load_sorts_desc(self, tmp_path):
        save_market_caps({"AAA": 1e9, "BBB": 5e11, "CCC": 2e10}, tmp_path)
        df = load_market_caps(tmp_path)
        assert list(df["ticker"]) == ["BBB", "CCC", "AAA"]
        assert list(df["market_cap"]) == [5e11, 2e10, 1e9]
        assert (df["fetched_at"] == df["fetched_at"].iloc[0]).all()

    def test_load_ignores_nonpositive_caps(self, tmp_path):
        (tmp_path / "market_cap.csv").write_text(
            "ticker,market_cap,fetched_at\nAAA,1000,2026-01-01\nBBB,0,2026-01-01\n"
        )
        df = load_market_caps(tmp_path)
        assert list(df["ticker"]) == ["AAA"]

    def test_load_returns_none_on_blank_cache(self, tmp_path):
        (tmp_path / "market_cap.csv").write_text("ticker,market_cap,fetched_at\n")
        assert load_market_caps(tmp_path) is None

    def test_market_cap_map(self, tmp_path):
        save_market_caps({"AAA": 1e9, "BBB": 5e11}, tmp_path)
        caps = market_cap_map(load_market_caps(tmp_path))
        assert caps == {"AAA": 1e9, "BBB": 5e11}
        assert market_cap_map(None) == {}

    def test_refresh_merges_and_keeps_missing(self, tmp_path, monkeypatch):
        save_market_caps({"AAA": 1e9, "BBB": 5e11}, tmp_path)

        def fake_fetch(tickers, workers=10, progress_cb=None):
            out = {}
            for t in tickers:
                if t == "AAA":
                    out[t] = 2e9  # refreshed
                # BBB intentionally missing from the fresh fetch
            return out

        monkeypatch.setattr("thermaltrend.company_universe.fetch_market_caps", fake_fetch)
        df = refresh_market_caps(tickers=["AAA", "BBB"], data_dir=tmp_path, keep_missing=True)
        caps = market_cap_map(df)
        assert caps["AAA"] == 2e9  # refreshed
        assert caps["BBB"] == 5e11  # kept from previous snapshot
        assert list(df["ticker"]) == ["BBB", "AAA"]  # still sorted desc

    def test_refresh_without_keep_missing(self, tmp_path, monkeypatch):
        save_market_caps({"AAA": 1e9}, tmp_path)

        def fake_fetch(tickers, workers=10, progress_cb=None):
            return {"BBB": 3e11}

        monkeypatch.setattr("thermaltrend.company_universe.fetch_market_caps", fake_fetch)
        df = refresh_market_caps(tickers=["AAA", "BBB"], data_dir=tmp_path, keep_missing=False)
        assert list(df["ticker"]) == ["BBB"]


class TestFormatMarketCap:
    def test_trillions(self):
        assert format_market_cap(4.9e12) == "$4.90T"

    def test_billions(self):
        assert format_market_cap(123.4e9) == "$123.4B"

    def test_millions(self):
        assert format_market_cap(567e6) == "$567M"

    def test_none_and_nan(self):
        assert format_market_cap(None) == "—"
        assert format_market_cap(float("nan")) == "—"


class TestCurrentMemberTickers:
    def test_restricted_to_members_with_data(self, tmp_path):
        _membership_csv(tmp_path)
        dates = pd.bdate_range("2021-01-01", periods=30)
        _make_parquet(tmp_path, "TEST", list(zip(dates, [100.0] * 30)))
        _make_parquet(tmp_path, "SPY", list(zip(dates, [400.0] * 30)))
        # GONE is in membership (removed) and has no parquet anyway.
        result = current_member_tickers(tmp_path, tmp_path / "membership.csv")
        assert result == ["TEST"]

    def test_falls_back_without_membership(self, tmp_path):
        dates = pd.bdate_range("2021-01-01", periods=30)
        _make_parquet(tmp_path, "TEST", list(zip(dates, [100.0] * 30)))
        _make_parquet(tmp_path, "SPY", list(zip(dates, [400.0] * 30)))
        missing = tmp_path / "no_membership.csv"
        result = current_member_tickers(tmp_path, membership_path=missing)
        assert result == ["TEST"]  # SPY excluded


class TestAnalyzeCompany:
    def test_all_strategies_run(self, tmp_path):
        dates = pd.bdate_range("2023-01-01", periods=700)
        closes = [100 + i * (0.4 if i % 10 < 5 else -0.3) for i in range(700)]
        _make_parquet(tmp_path, "TEST", list(zip(dates, closes)))
        _make_parquet(tmp_path, "SPY", list(zip(dates, closes)))

        results = analyze_company(
            "TEST", "2023-01-01", "2025-12-31", data_dir=str(tmp_path)
        )
        assert set(results.keys()) == set(DEFAULT_STRATEGY_LABELS)
        for label, result in results.items():
            assert "metrics" in result
            assert "trades" in result
            assert "equity_curve" in result
            assert isinstance(result["equity_curve"], pd.Series)

    def test_unknown_ticker_returns_empty_results(self, tmp_path):
        results = analyze_company(
            "NOPE", "2023-01-01", "2025-12-31", data_dir=str(tmp_path)
        )
        assert set(results.keys()) == set(DEFAULT_STRATEGY_LABELS)
        for result in results.values():
            assert result["metrics"]["total_trades"] == 0
            assert result["trades"] == []

    def test_point_in_time_universe_runs(self, tmp_path):
        dates = pd.bdate_range("2023-01-01", periods=700)
        closes = [100 + i * (0.4 if i % 10 < 5 else -0.3) for i in range(700)]
        _make_parquet(tmp_path, "TEST", list(zip(dates, closes)))
        _make_parquet(tmp_path, "SPY", list(zip(dates, closes)))
        _membership_csv(tmp_path)

        results = analyze_company(
            "TEST", "2023-01-01", "2025-12-31",
            universe="point_in_time", data_dir=str(tmp_path),
        )
        assert set(results.keys()) == set(DEFAULT_STRATEGY_LABELS)

    def test_subset_of_strategies(self, tmp_path):
        dates = pd.bdate_range("2023-01-01", periods=700)
        closes = [100 + i * (0.4 if i % 10 < 5 else -0.3) for i in range(700)]
        _make_parquet(tmp_path, "TEST", list(zip(dates, closes)))
        _make_parquet(tmp_path, "SPY", list(zip(dates, closes)))

        results = analyze_company(
            "TEST", "2023-01-01", "2025-12-31",
            data_dir=str(tmp_path), strategy_labels=["MA 50/200", "RSI 14"],
        )
        assert set(results.keys()) == {"MA 50/200", "RSI 14"}


class TestCompanySummaryFrame:
    def test_columns_and_sorting(self, tmp_path):
        dates = pd.bdate_range("2023-01-01", periods=700)
        closes = [100 + i * (0.4 if i % 10 < 5 else -0.3) for i in range(700)]
        _make_parquet(tmp_path, "TEST", list(zip(dates, closes)))
        _make_parquet(tmp_path, "SPY", list(zip(dates, closes)))

        results = analyze_company(
            "TEST", "2023-01-01", "2025-12-31", data_dir=str(tmp_path)
        )
        summary = company_summary_frame(results)
        expected = {
            "strategy", "total_trades", "trades_completed", "win_rate",
            "total_pnl", "avg_trade_pnl", "cagr", "sharpe", "max_drawdown",
        }
        assert set(summary.columns) == expected
        assert len(summary) == len(DEFAULT_STRATEGY_LABELS)
        # sorted by total P&L, descending
        assert summary["total_pnl"].is_monotonic_decreasing

    def test_empty_results(self):
        summary = company_summary_frame({})
        assert summary.empty


class TestTradesToFrame:
    def test_excludes_open_trades(self):
        from datetime import datetime

        from thermaltrend.analytics.trade_simulator import Trade
        from thermaltrend.core.events import SignalDirection

        closed = Trade(
            ticker="TEST", entry_date=datetime(2026, 1, 5), entry_price=100.0,
            exit_date=datetime(2026, 1, 15), exit_price=110.0,
            direction=SignalDirection.BUY, pnl=1000.0, pnl_pct=0.10,
            holding_days=10, exit_reason="signal", strategy_id="test", shares=100,
        )
        open_trade = Trade(
            ticker="TEST", entry_date=datetime(2026, 2, 1), entry_price=100.0,
            exit_date=datetime(2026, 3, 1), exit_price=105.0,
            direction=SignalDirection.BUY, pnl=500.0, pnl_pct=0.05,
            holding_days=29, exit_reason="data_end", strategy_id="test", shares=100,
        )

        frame = trades_to_frame([closed, open_trade])
        assert len(frame) == 1
        assert set(frame.columns) == {
            "Entry Date", "Entry Price", "Exit Date", "Exit Price",
            "P&L ($)", "P&L (%)", "Holding (days)", "Exit Reason",
        }
        assert frame.iloc[0]["Exit Reason"] == "signal"

    def test_empty_trades(self):
        assert trades_to_frame([]).empty


def _summary_frame(rows):
    return pd.DataFrame(rows)


class TestRecommendStrategy:
    def test_empty_summary(self):
        assert recommend_strategy(pd.DataFrame()) is None

    def test_picks_best_qualified_strategy(self):
        summary = _summary_frame(
            [
                {"strategy": "A", "trades_completed": 5, "win_rate": 0.6,
                 "total_pnl": 10_000, "cagr": 0.20, "sharpe": 1.2,
                 "max_drawdown": -0.15},
                {"strategy": "B", "trades_completed": 6, "win_rate": 0.4,
                 "total_pnl": 4_000, "cagr": 0.10, "sharpe": 0.3,
                 "max_drawdown": -0.35},
                {"strategy": "C", "trades_completed": 1, "win_rate": 1.0,
                 "total_pnl": 50_000, "cagr": 0.90, "sharpe": 9.0,
                 "max_drawdown": -0.01},
            ]
        )
        rec = recommend_strategy(summary)
        assert rec is not None
        assert rec["strategy"] == "A"
        assert rec["low_confidence"] is False
        assert rec["trades_completed"] == 5
        assert rec["reasons"]

    def test_ignores_sparse_lucky_strategy(self):
        # C has a huge single-trade pnl but never reaches the trade threshold.
        summary = _summary_frame(
            [
                {"strategy": "A", "trades_completed": 50, "win_rate": 0.55,
                 "total_pnl": 20_000, "cagr": 0.12, "sharpe": 0.8,
                 "max_drawdown": -0.12},
                {"strategy": "C", "trades_completed": 2, "win_rate": 1.0,
                 "total_pnl": 90_000, "cagr": 0.90, "sharpe": 9.0,
                 "max_drawdown": -0.01},
            ]
        )
        rec = recommend_strategy(summary)
        assert rec["strategy"] == "A"

    def test_low_confidence_when_no_strategy_qualifies(self):
        summary = _summary_frame(
            [
                {"strategy": "A", "trades_completed": 0, "win_rate": 0.0,
                 "total_pnl": 0.0, "cagr": 0.0, "sharpe": 0.0,
                 "max_drawdown": 0.0},
                {"strategy": "B", "trades_completed": 2, "win_rate": 0.5,
                 "total_pnl": -100.0, "cagr": -0.01, "sharpe": -0.2,
                 "max_drawdown": -0.02},
            ]
        )
        rec = recommend_strategy(summary)
        assert rec is not None
        assert rec["low_confidence"] is True
        assert rec["strategy"] == "B"
        assert any("caution" in r for r in rec["reasons"])

    def test_custom_min_trades(self):
        summary = _summary_frame(
            [
                {"strategy": "A", "trades_completed": 4, "win_rate": 0.5,
                 "total_pnl": 100.0, "cagr": 0.01, "sharpe": 0.2,
                 "max_drawdown": -0.05},
                {"strategy": "B", "trades_completed": 10, "win_rate": 0.6,
                 "total_pnl": 200.0, "cagr": 0.02, "sharpe": 0.5,
                 "max_drawdown": -0.08},
            ]
        )
        assert recommend_strategy(summary, min_trades=8)["strategy"] == "B"

    def test_negative_pnl_flagged_least_bad(self):
        summary = _summary_frame(
            [
                {"strategy": "A", "trades_completed": 5, "win_rate": 0.4,
                 "total_pnl": -200.0, "cagr": -0.02, "sharpe": -0.4,
                 "max_drawdown": -0.20},
                {"strategy": "B", "trades_completed": 5, "win_rate": 0.5,
                 "total_pnl": -50.0, "cagr": -0.01, "sharpe": -0.1,
                 "max_drawdown": -0.10},
            ]
        )
        rec = recommend_strategy(summary)
        assert rec["strategy"] == "B"
        assert rec["pnl_word"] == "the least-bad"

    def test_default_min_trades_constant(self):
        assert MIN_TRADES_FOR_CONFIDENCE == 3