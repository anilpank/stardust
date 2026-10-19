"""Tests for landing/generate_portfolio_snapshot.py — the shadow-run snapshot."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from landing import generate_portfolio_snapshot as gen
from thermaltrend.portfolio.config import PortfolioConfig


def _make_parquet(tmp_path, ticker, dates, closes):
    df = pd.DataFrame(
        {
            "Open": list(closes),
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": list(closes),
            "Volume": [1000] * len(dates),
            "ticker": ticker,
        },
        index=pd.DatetimeIndex(dates, name="Date"),
    )
    df.to_parquet(tmp_path / f"{ticker}.parquet")


@pytest.fixture
def data_dir(tmp_path):
    dates = pd.bdate_range("2025-01-02", periods=120)
    wave = 100.0 + 10.0 * np.sin(np.arange(120) / 5.0)
    _make_parquet(tmp_path, "TEST", dates, [float(c) for c in wave])
    _make_parquet(
        tmp_path, "SPY", dates,
        [float(c) for c in 400.0 + 20.0 * np.sin(np.arange(120) / 5.0)],
    )
    return tmp_path


def _config(**overrides):
    base = dict(
        capital=10_000.0,
        core_ticker="SPY",
        core_target_weight=0.25,
        core_momentum_lookback=5,
        slippage_bps=10.0,
        cash_yield=0.04,
    )
    base.update(overrides)
    return PortfolioConfig(**base)


def _fill(date, ticker="SPY", side="buy", shares=10, price=100.0, **rest):
    row = {
        "trade_date": date, "ticker": ticker, "lane": "core", "side": side,
        "shares": shares, "price": price, "created_at": f"{date}T20:00:00+00:00",
        "source": "live", "proposal_id": "", "note": "",
    }
    row.update(rest)
    return row


def _fake_model(config, dates, equities, *, pending=(), cash=5000.0,
                core_invested=2500.0, exposure=0.25, trades=()):
    index = pd.DatetimeIndex(dates)
    daily = pd.DataFrame(
        {
            "date": index,
            "equity": equities,
            "cash": [cash] * len(index),
            "invested": [core_invested] * len(index),
            "core_invested": [core_invested] * len(index),
            "exposure": [exposure] * len(index),
        }
    )
    return SimpleNamespace(
        config=config,
        daily=daily,
        equity_curve=pd.Series([config.capital, *equities], index=index.insert(0, index[0] - pd.Timedelta(days=1))),
        rejected=list(pending),
        core_history=[{"date": index[-1], "on": True, "momentum": 0.08, "regime": "bull"}],
        trades=list(trades),
        metrics={"equity_end": equities[-1], "cagr": 0.06},
    )


class TestSeriesMetrics:
    def test_empty(self):
        metrics = gen._series_metrics(pd.Series(dtype=float))
        assert metrics["cagr"] == 0.0 and metrics["equity_end"] is None

    def test_doubling(self):
        index = pd.bdate_range("2025-01-02", periods=253)
        equity = pd.Series(np.linspace(100.0, 200.0, 253), index=index)
        metrics = gen._series_metrics(equity)
        assert metrics["equity_start"] == 100.0
        assert metrics["equity_end"] == 200.0
        assert metrics["cagr"] == pytest.approx(1.0, abs=0.02)
        assert metrics["max_drawdown"] <= 0.0

    def test_drawdown(self):
        index = pd.bdate_range("2025-01-02", periods=4)
        equity = pd.Series([100.0, 120.0, 90.0, 110.0], index=index)
        assert gen._series_metrics(equity)["max_drawdown"] == pytest.approx(-0.25)


class TestNextSession:
    def test_weekday(self):
        assert gen._next_session("2026-10-15").date().isoformat() == "2026-10-16"

    def test_friday_skips_weekend(self):
        assert gen._next_session("2026-10-16").date().isoformat() == "2026-10-19"


class TestLoadJournal:
    def test_local_dir(self, tmp_path):
        (tmp_path / "journal_fills.csv").write_text(
            "created_at,trade_date,ticker,lane,side,shares,price,source,proposal_id,note\n"
            "x,2026-01-05,spy,core,buy,40,580.0,live,p1,n\n",
            encoding="utf-8",
        )
        (tmp_path / "journal_decisions.csv").write_text(
            "created_at,trade_date,proposal_id,action,ticker,lane,side,planned_shares,planned_price,note\n"
            "x,2026-01-05,p1,acted,SPY,core,buy,40,580.0,\n",
            encoding="utf-8",
        )
        journal = gen.load_journal(journal_dir=tmp_path)
        assert journal.source == "loaded"
        assert journal.fills[0]["ticker"] == "SPY"
        assert journal.fills[0]["shares"] == 40
        assert journal.fills[0]["price"] == 580.0
        assert journal.decisions[0]["action"] == "acted"

    def test_missing_dir_is_empty(self, tmp_path):
        assert gen.load_journal(journal_dir=tmp_path / "nope").source == "empty"

    def test_http_uses_export_endpoints(self, monkeypatch):
        seen = []

        def fake(url, user, password, timeout):
            seen.append(url)
            if url.endswith("fills.csv"):
                return [{"trade_date": "2026-01-05", "ticker": "SPY", "side": "buy",
                         "shares": "3", "price": "10"}]
            return []

        monkeypatch.setattr(gen, "_http_get_csv", fake)
        journal = gen.load_journal(base_url="http://x/", user="admin", password="pw")
        assert seen == [
            "http://x/api/admin/trade/export/fills.csv",
            "http://x/api/admin/trade/export/decisions.csv",
        ]
        assert journal.fills[0]["shares"] == 3

    def test_http_error_is_captured(self, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(gen, "_http_get_csv", boom)
        journal = gen.load_journal(base_url="http://x")
        assert journal.source == "error"
        assert "connection refused" in journal.load_error


class TestReplayFills:
    def _closes(self):
        dates = pd.bdate_range("2026-01-05", periods=10)
        return dates, {"SPY": pd.Series(np.linspace(100.0, 118.0, 10), index=dates)}

    def test_empty(self):
        dates, closes = self._closes()
        ledger = gen.replay_fills([], _config(), closes, as_of=dates[-1])
        assert len(ledger.equity) == 0
        assert ledger.cash == 10_000.0
        assert ledger.positions == []

    def test_buy_and_mark(self):
        dates, closes = self._closes()
        fills = [_fill(dates[0].date().isoformat(), shares=10, price=100.0)]
        ledger = gen.replay_fills(fills, _config(), closes, as_of=dates[-1])
        assert ledger.source == "journal"
        assert ledger.fills_applied == 1
        assert ledger.cash == pytest.approx(9_000.0)
        assert ledger.equity.iloc[0] == pytest.approx(10_000.0)
        assert ledger.equity.iloc[-1] == pytest.approx(9_000.0 + 10 * 118.0)
        assert ledger.positions[0]["shares"] == 10
        assert ledger.positions[0]["market_value"] == pytest.approx(1_180.0)

    def test_calendar_starts_at_first_fill(self):
        dates, closes = self._closes()
        fills = [_fill(dates[3].date().isoformat(), shares=1, price=100.0)]
        ledger = gen.replay_fills(fills, _config(), closes, as_of=dates[-1])
        assert ledger.equity.index[0] == dates[3]

    def test_average_entry_across_buys(self):
        dates, closes = self._closes()
        fills = [
            _fill(dates[0].date().isoformat(), shares=10, price=100.0),
            _fill(dates[1].date().isoformat(), shares=10, price=120.0),
        ]
        ledger = gen.replay_fills(fills, _config(), closes, as_of=dates[-1])
        assert ledger.positions[0]["shares"] == 20
        assert ledger.positions[0]["avg_entry"] == pytest.approx(110.0)
        assert ledger.cash == pytest.approx(10_000.0 - 1_000.0 - 1_200.0)

    def test_full_sell_closes_position(self):
        dates, closes = self._closes()
        fills = [
            _fill(dates[0].date().isoformat(), shares=10, price=100.0),
            _fill(dates[2].date().isoformat(), side="sell", shares=10, price=110.0),
        ]
        ledger = gen.replay_fills(fills, _config(), closes, as_of=dates[-1])
        assert ledger.positions == []
        assert ledger.cash == pytest.approx(10_100.0)

    def test_sell_without_position_is_rejected(self):
        dates, closes = self._closes()
        fills = [_fill(dates[0].date().isoformat(), side="sell", shares=5, price=100.0)]
        ledger = gen.replay_fills(fills, _config(), closes, as_of=dates[-1])
        assert ledger.rejected_fills == 1
        assert ledger.cash == 10_000.0

    def test_bad_fill_is_rejected(self):
        dates, closes = self._closes()
        fills = [_fill(dates[0].date().isoformat(), side="buy", shares=5, price=0.0)]
        ledger = gen.replay_fills(fills, _config(), closes, as_of=dates[-1])
        assert ledger.rejected_fills == 1

    def test_missing_core_prices_raises(self):
        with pytest.raises(ValueError):
            gen.replay_fills([], _config(), {}, as_of=None)


class TestBuildProposal:
    def test_pending_core_order(self):
        dates = pd.bdate_range("2025-01-02", periods=5)
        model = _fake_model(
            _config(), dates, [10_000.0] * 5,
            pending=[{"date": dates[-1], "ticker": "SPY", "side": "buy",
                      "lane": "core", "reason": "data_end"}],
        )
        proposal = gen.build_proposal(model, _config(), ref_price=405.0)
        assert proposal["side"] == "buy"
        assert proposal["ticker"] == "SPY"
        assert proposal["for_session"] == "2025-01-09"
        assert proposal["ref_price"] == 405.0
        assert "momentum" in proposal["reason"]

    def test_no_pending_is_hold(self):
        dates = pd.bdate_range("2025-01-02", periods=5)
        model = _fake_model(_config(), dates, [10_000.0] * 5)
        assert gen.build_proposal(model, _config()) is None


class TestBuildSnapshot:
    def test_paper_mode_from_real_model(self, data_dir):
        config = _config()
        model = gen.run_model(config, data_dir, start="2025-01-02")
        payload = gen.build_snapshot(config, data_dir=data_dir, mode="paper",
                                     model_result=model)
        assert payload["mode"] == "paper"
        assert payload["live"]["source"] == "model"
        assert payload["adherence"]["exposure_match_pct"] == 1.0
        assert payload["fills"]
        assert payload["journal_source"] == "empty"
        assert set(payload) >= {
            "generated_at", "as_of", "mode", "config", "model", "live",
            "proposal", "adherence", "journal_source", "fills",
        }
        assert len(payload["model"]["equity"]["dates"]) == len(
            payload["model"]["equity"]["values"]
        )
        json.dumps(payload)

    def test_live_mode_replays_journal(self, data_dir, tmp_path):
        config = _config()
        model = gen.run_model(config, data_dir, start="2025-01-02")
        as_of = pd.Timestamp(model.daily["date"].iloc[-1])
        (tmp_path / "journal_fills.csv").write_text(
            "created_at,trade_date,ticker,lane,side,shares,price,source,proposal_id,note\n"
            f"x,{as_of.date()},SPY,core,buy,5,400.0,live,,\n",
            encoding="utf-8",
        )
        (tmp_path / "journal_decisions.csv").write_text(
            "created_at,trade_date,proposal_id,action,ticker,lane,side,planned_shares,planned_price,note\n"
            f"x,{as_of.date()},p1,acted,SPY,core,buy,5,400.0,\n",
            encoding="utf-8",
        )
        journal = gen.load_journal(journal_dir=tmp_path)
        payload = gen.build_snapshot(config, data_dir=data_dir, journal=journal,
                                     mode="live", model_result=model)
        assert payload["live"]["source"] == "journal"
        assert payload["live"]["positions"][0]["shares"] == 5
        assert payload["adherence"]["decisions"] == 1
        assert payload["adherence"]["acted_pct"] == 1.0

    def test_invalid_mode_raises(self, data_dir):
        model = gen.run_model(_config(), data_dir, start="2025-01-02")
        with pytest.raises(ValueError):
            gen.build_snapshot(_config(), data_dir=data_dir, mode="sneaky",
                               model_result=model)


class TestSaveSnapshot:
    def test_round_trip(self, tmp_path):
        path = gen.save_snapshot({"a": 1}, tmp_path / "out.json")
        assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}

    def test_creates_parent_dirs(self, tmp_path):
        path = gen.save_snapshot({"a": 1}, tmp_path / "a" / "b" / "out.json")
        assert path.exists()

    def test_default_out_is_private(self):
        assert gen.DEFAULT_OUT.parent.name == "private"
        assert gen.DEFAULT_OUT.name == "portfolio_snapshot.json"

    def test_committed_snapshot_is_valid_if_present(self):
        if not gen.DEFAULT_OUT.exists():
            pytest.skip("portfolio_snapshot.json not generated")
        payload = json.loads(gen.DEFAULT_OUT.read_text(encoding="utf-8"))
        for key in (
            "generated_at", "as_of", "mode", "config", "model", "live",
            "adherence", "fills",
        ):
            assert key in payload, key
        assert payload["mode"] in gen.MODE_CHOICES
        assert len(payload["model"]["equity"]["dates"]) == len(
            payload["model"]["equity"]["values"]
        )
