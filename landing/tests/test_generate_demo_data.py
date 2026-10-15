"""Tests for landing/generate_demo_data.py.

Pure helpers (date windows, signal/metric/trade extraction) are unit-tested with
synthetic inputs. The ``build`` integration test runs the real stardust engine
over one ticker's Parquet data and is skipped when that data is unavailable. The
``main`` tests stub the engine out to verify the CLI's file-writing contract
(both files, ``--skip-trades``, ``--trades-output`` and ``--resume``).
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from landing import generate_demo_data as gen
from thermaltrend.analytics.trade_simulator import Trade
from thermaltrend.core.events import SignalDirection, SignalEvent

REAL_DATA_DIR = gen.DEFAULT_DATA_DIR


def _write_parquet(tmp_path: Path, ticker: str, dates: list[str]) -> Path:
    df = pd.DataFrame(
        {
            "Open": [100.0] * len(dates),
            "High": [101.0] * len(dates),
            "Low": [99.0] * len(dates),
            "Close": [100.0] * len(dates),
            "Volume": [1000] * len(dates),
            "ticker": ticker,
        },
        index=pd.DatetimeIndex(dates, name="Date"),
    )
    path = tmp_path / f"{ticker}.parquet"
    df.to_parquet(path)
    return path


class TestWindowStart:
    def test_one_year(self):
        assert gen._window_start(pd.Timestamp("2026-09-18"), 1) == "2025-09-18"

    def test_ten_years(self):
        assert gen._window_start(pd.Timestamp("2026-09-18"), 10) == "2016-09-18"


class TestLatestDate:
    def test_missing_file_returns_none(self, tmp_path):
        assert gen._latest_date("NOPE", tmp_path) is None

    def test_returns_last_date(self, tmp_path):
        _write_parquet(tmp_path, "TEST", ["2020-01-02", "2020-01-03", "2020-01-06"])
        assert gen._latest_date("TEST", tmp_path) == pd.Timestamp("2020-01-06")


class TestHoldoutStart:
    def test_missing_file_returns_none(self, tmp_path):
        assert gen._holdout_start("NOPE", tmp_path, "2020-01-01") is None

    def test_skips_when_too_few_rows(self, tmp_path):
        _write_parquet(tmp_path, "TEST", ["2020-01-02", "2020-01-03"])
        assert gen._holdout_start("TEST", tmp_path, "2020-01-01") is None

    def test_split_is_about_seventy_percent(self, tmp_path):
        dates = pd.bdate_range("2020-01-01", periods=100).strftime("%Y-%m-%d").tolist()
        _write_parquet(tmp_path, "TEST", dates)
        window_start = dates[10]
        split = gen._holdout_start("TEST", tmp_path, window_start)
        assert split is not None
        window_dates = [d for d in dates if d >= window_start]
        before = [d for d in window_dates if d < split]
        assert 0.6 <= len(before) / len(window_dates) <= 0.8


class TestMetrics:
    def test_extracts_and_rounds(self):
        result = {"metrics": {
            "win_rate": 0.66666, "total_return": 1234.567,
            "cagr": 0.01234, "sharpe": -1.23456, "trades_completed": 7.0,
        }}
        out = gen._metrics(result)
        assert out == {
            "win_rate": 0.6667, "total_pnl": 1234.57,
            "cagr": 0.0123, "sharpe": -1.235, "trades_completed": 7,
        }

    def test_empty_metrics(self):
        out = gen._metrics({})
        assert out["trades_completed"] == 0
        assert out["total_pnl"] == 0.0

    def test_non_finite_becomes_none(self):
        out = gen._metrics({"metrics": {
            "win_rate": float("nan"), "total_return": float("inf"),
            "cagr": float("nan"), "sharpe": float("nan"), "trades_completed": 7,
        }})
        assert out == {
            "win_rate": None, "total_pnl": None, "cagr": None,
            "sharpe": None, "trades_completed": 7,
        }


class TestOosPass:
    def test_passes_only_when_return_and_win_rate_hold(self):
        assert gen._oos_pass({"total_pnl": 10.0, "win_rate": 0.6}) is True
        assert gen._oos_pass({"total_pnl": -1.0, "win_rate": 0.9}) is False
        assert gen._oos_pass({"total_pnl": 1.0, "win_rate": 0.4}) is False

    def test_non_finite_metrics_are_false_not_a_crash(self):
        assert gen._oos_pass({"total_pnl": None, "win_rate": None}) is False
        assert gen._oos_pass({"total_pnl": None, "win_rate": 0.9}) is False


class TestLatestSignal:
    def _signal(self, day, direction, strength):
        return SignalEvent(
            timestamp=datetime(2026, 8, day),
            ticker="AAPL",
            direction=direction,
            strength=strength,
            strategy_id="test",
        )

    def test_picks_most_recent(self):
        result = {
            "strategy_name": "Donchian 20/10",
            "signals": [
                self._signal(1, SignalDirection.SELL, 0.2),
                self._signal(14, SignalDirection.BUY, 0.8),
            ],
        }
        sig = gen._latest_signal(result)
        assert sig["direction"] == "BUY"
        assert sig["as_of"] == "2026-08-14"
        assert sig["strength"] == 0.8

    def test_no_signals_is_hold(self):
        sig = gen._latest_signal({"strategy_name": "MA 50/200", "signals": []})
        assert sig["direction"] == "HOLD"
        assert sig["as_of"] is None

    def test_non_finite_strength_becomes_none(self):
        result = {"strategy_name": "X", "signals": [self._signal(3, SignalDirection.BUY, float("nan"))]}
        assert gen._latest_signal(result)["strength"] is None


class TestConsensusSignal:
    def _call(self, direction, as_of):
        return {"direction": direction, "strength": 0.5, "as_of": as_of}

    def test_majority_sets_direction(self):
        calls = {
            "1y": self._call("BUY", "2026-09-01"),
            "3y": self._call("BUY", "2026-08-15"),
            "5y": self._call("SELL", "2026-07-24"),
            "10y": self._call("BUY", "2026-06-10"),
        }
        sig = gen._consensus_signal(calls)
        assert sig == {
            "direction": "BUY", "agree": 3, "total": 4,
            "consensus": True, "as_of": "2026-09-01",
        }

    def test_half_is_not_a_majority(self):
        calls = {
            "1y": self._call("BUY", "2026-09-01"),
            "3y": self._call("BUY", "2026-08-15"),
            "5y": self._call("SELL", "2026-07-24"),
            "10y": self._call("SELL", "2026-06-10"),
        }
        sig = gen._consensus_signal(calls)
        assert sig["direction"] is None
        assert sig["consensus"] is False
        assert sig["agree"] == 2 and sig["total"] == 4

    def test_winners_without_a_call_abstain_but_count(self):
        calls = {
            "1y": self._call("BUY", "2026-09-01"),
            "3y": {"direction": "HOLD", "strength": 0.0, "as_of": None},
            "5y": {"direction": "HOLD", "strength": 0.0, "as_of": None},
            "10y": None,
        }
        sig = gen._consensus_signal(calls)
        assert sig["direction"] is None
        assert sig["consensus"] is False
        assert sig["agree"] == 1 and sig["total"] == 3

    def test_no_calls_at_all(self):
        assert gen._consensus_signal({}) == gen._empty_signal()
        assert gen._consensus_signal(
            {"1y": {"direction": "HOLD", "strength": 0.0, "as_of": None}}
        ) == gen._empty_signal()


class TestBuildIntegration:
    @pytest.mark.slow
    def test_build_produces_windows_for_real_ticker(self, tmp_path):
        if not (REAL_DATA_DIR / "AAPL.parquet").exists():
            pytest.skip("AAPL Parquet data not available")

        payload, trades_payload = gen.build(["AAPL"], REAL_DATA_DIR, skip_oos=True, min_trades=3)
        entry = payload["tickers"]["AAPL"]

        assert payload["oos_definition"]
        assert entry["name"]
        sig = entry["signal"]
        assert sig["direction"] in {"BUY", "SELL", "HOLD", None}
        assert sig["total"] >= sig["agree"] >= 0
        assert sig["consensus"] == (sig["direction"] is not None)
        # every window is present; each either has a winner or is explicitly empty
        for w in gen.WINDOWS:
            cell = entry["windows"][w]
            assert "strategy" in cell
            if cell["strategy"]:
                assert "win_rate" in cell and "trades_completed" in cell
                assert "call" in cell
                assert cell["call"]["direction"] in {"BUY", "SELL", "HOLD"}

        # trades file mirrors the signals file, with positional rows
        assert trades_payload["columns"] == gen.TRADE_COLUMNS
        assert "AAPL" in trades_payload["trades"]
        for rows in trades_payload["trades"]["AAPL"].values():
            assert rows
            assert all(len(row) == len(gen.TRADE_COLUMNS) for row in rows)


class TestTrades:
    def _trade(self, **overrides):
        base = dict(
            ticker="AAPL", entry_date=datetime(2024, 1, 2), entry_price=100.0,
            exit_date=datetime(2024, 1, 20), exit_price=110.0,
            direction=SignalDirection.BUY, pnl=1000.0, pnl_pct=0.10,
            holding_days=18, exit_reason="signal", strategy_id="test",
        )
        base.update(overrides)
        return Trade(**base)

    def test_row_shape_matches_columns(self):
        rows = gen._trades({"trades": [self._trade()]})
        assert len(rows) == 1
        assert len(rows[0]) == len(gen.TRADE_COLUMNS)
        assert rows[0][0] == "2024-01-02"   # Entry Date
        assert rows[0][1] == 100.0          # Entry Price
        assert rows[0][4] == 1000.0         # P&L ($)
        assert rows[0][7] == "signal"       # Exit Reason

    def test_excludes_open_data_end_trades(self):
        rows = gen._trades({"trades": [self._trade(), self._trade(exit_reason="data_end")]})
        assert len(rows) == 1

    def test_non_finite_cells_become_none(self):
        rows = gen._trades({"trades": [self._trade(
            exit_price=float("nan"), pnl=float("nan"), pnl_pct=float("nan"))]})
        assert rows[0][3] is None   # Exit Price
        assert rows[0][4] is None   # P&L ($)
        assert rows[0][5] is None   # P&L (%)

    def test_newest_first(self):
        old = self._trade(entry_date=datetime(2023, 1, 3), exit_date=datetime(2023, 2, 1))
        new = self._trade(entry_date=datetime(2024, 1, 2), exit_date=datetime(2024, 1, 20))
        rows = gen._trades({"trades": [old, new]})
        assert [r[0] for r in rows] == ["2024-01-02", "2023-01-03"]

    def test_empty_inputs(self):
        assert gen._trades({}) == []
        assert gen._trades({"trades": []}) == []


class TestEmptyTradesPayload:
    def test_structure(self):
        payload = gen._empty_trades_payload()
        assert payload["columns"] == gen.TRADE_COLUMNS
        assert payload["trades"] == {}
        assert datetime.fromisoformat(payload["generated_at"])


def _fake_entry(ticker: str) -> dict:
    return {
        "name": f"{ticker} Inc.",
        "signal": {"direction": "BUY", "agree": 1, "total": 1, "consensus": True,
                   "as_of": "2026-01-01"},
        "windows": {
            "1y": {"strategy": "MA 50/200", "low_confidence": False, "oos": None,
                   "call": {"direction": "BUY", "strength": 0.5, "as_of": "2026-01-01"},
                   "win_rate": 0.5, "total_pnl": 12.0, "cagr": 0.1, "sharpe": 1.0,
                   "trades_completed": 1},
        },
    }


def _fake_trades() -> dict:
    return {"1y": [["2026-01-02", 100.0, "2026-01-10", 105.0, 5.0, 5.0, 8, "signal"]]}


class TestBuild:
    def test_mirrors_signals_and_trades(self, monkeypatch):
        def fake(ticker, data_dir, skip_oos, min_trades):
            return None if ticker == "NOPE" else (_fake_entry(ticker), _fake_trades())

        monkeypatch.setattr(gen, "build_ticker", fake)
        signals, trades = gen.build(["AAPL", "NOPE"], Path("."), skip_oos=False, min_trades=3)
        assert set(signals["tickers"]) == {"AAPL"}
        assert set(trades["trades"]) == {"AAPL"}
        assert trades["columns"] == gen.TRADE_COLUMNS


class TestSafeBuild:
    def test_failure_is_captured_not_raised(self, monkeypatch):
        def boom(*a, **k):
            raise ValueError("kaboom")

        monkeypatch.setattr(gen, "build_ticker", boom)
        result = gen._safe_build("AAPL", Path("."), False, 3)
        assert isinstance(result, gen.TickerFailure)
        assert result.ticker == "AAPL"
        assert "ValueError: kaboom" in result.error

    def test_success_passes_through(self, monkeypatch):
        monkeypatch.setattr(gen, "build_ticker", lambda *a, **k: ("entry", {}))
        assert gen._safe_build("AAPL", Path("."), False, 3) == ("entry", {})


class TestMain:
    @pytest.fixture
    def stub(self, monkeypatch):
        """Replace the engine-backed build with a deterministic stub, recording calls."""
        calls = []

        def fake(ticker, data_dir, skip_oos, min_trades):
            calls.append(ticker)
            return _fake_entry(ticker), _fake_trades()

        monkeypatch.setattr(gen, "build_ticker", fake)
        return calls

    def test_writes_both_files(self, tmp_path, stub):
        sig, trd = tmp_path / "demo_signals.json", tmp_path / "demo_trades.json"
        gen.main(["--tickers", "AAPL", "NVDA",
                  "--output", str(sig), "--trades-output", str(trd)])
        signals, trades = json.loads(sig.read_text()), json.loads(trd.read_text())
        assert set(signals["tickers"]) == {"AAPL", "NVDA"}
        assert set(trades["trades"]) == {"AAPL", "NVDA"}
        assert signals["oos_definition"]
        assert trades["columns"] == gen.TRADE_COLUMNS
        assert stub == ["AAPL", "NVDA"]

    def test_skip_trades_writes_only_signals(self, tmp_path, stub):
        sig, trd = tmp_path / "s.json", tmp_path / "t.json"
        gen.main(["--tickers", "AAPL", "--output", str(sig),
                  "--trades-output", str(trd), "--skip-trades"])
        assert sig.exists()
        assert not trd.exists()

    def test_resume_skips_existing_tickers(self, tmp_path, stub):
        sig, trd = tmp_path / "s.json", tmp_path / "t.json"
        sig.write_text(json.dumps({
            "generated_at": "2026-01-01T00:00:00+00:00", "oos_definition": "x",
            "tickers": {"AAPL": _fake_entry("AAPL")},
        }))
        gen.main(["--tickers", "AAPL", "NVDA", "--output", str(sig),
                  "--trades-output", str(trd), "--resume"])
        assert stub == ["NVDA"]                      # AAPL was kept, not recomputed
        assert set(json.loads(sig.read_text())["tickers"]) == {"AAPL", "NVDA"}

    def test_resume_preserves_existing_trades(self, tmp_path, stub):
        sig, trd = tmp_path / "s.json", tmp_path / "t.json"
        sig.write_text(json.dumps({
            "generated_at": "2026-01-01T00:00:00+00:00", "oos_definition": "x",
            "tickers": {"AAPL": _fake_entry("AAPL")},
        }))
        trd.write_text(json.dumps({
            "generated_at": "2026-01-01T00:00:00+00:00", "columns": gen.TRADE_COLUMNS,
            "trades": {"AAPL": {"1y": [["2025-01-02", 1.0, "2025-01-03", 2.0, 1.0, 1.0, 1, "signal"]]}},
        }))
        gen.main(["--tickers", "AAPL", "NVDA", "--output", str(sig),
                  "--trades-output", str(trd), "--resume"])
        trades = json.loads(trd.read_text())
        assert set(trades["trades"]) == {"AAPL", "NVDA"}
        assert trades["trades"]["AAPL"]["1y"][0][0] == "2025-01-02"  # not overwritten

    def test_no_data_ticker_is_omitted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gen, "build_ticker", lambda *a, **k: None)
        sig, trd = tmp_path / "s.json", tmp_path / "t.json"
        gen.main(["--tickers", "ZZZZ", "--output", str(sig), "--trades-output", str(trd)])
        assert json.loads(sig.read_text())["tickers"] == {}
        assert json.loads(trd.read_text())["trades"] == {}

    def test_failed_ticker_exits_nonzero(self, tmp_path, monkeypatch):
        def boom(ticker, *a, **k):
            if ticker == "BOOM":
                raise ValueError("kaboom")
            return _fake_entry(ticker), _fake_trades()

        monkeypatch.setattr(gen, "build_ticker", boom)
        sig, trd = tmp_path / "s.json", tmp_path / "t.json"
        with pytest.raises(SystemExit) as exc:
            gen.main(["--tickers", "AAPL", "BOOM", "--output", str(sig),
                      "--trades-output", str(trd), "--skip-oos"])
        assert exc.value.code == 1
        # the good ticker is still written (resumable), but the non-zero exit
        # signals that the universe is incomplete and must not be published
        assert set(json.loads(sig.read_text())["tickers"]) == {"AAPL"}

    def test_workers_writes_all_in_request_order(self, tmp_path, stub, monkeypatch):
        # Run the parallel path in-process (threads) so the stubbed engine is visible.
        monkeypatch.setattr(gen, "_pool_factory", ThreadPoolExecutor)
        sig, trd = tmp_path / "s.json", tmp_path / "t.json"
        gen.main(["--tickers", "KO", "AAPL", "NVDA", "--output", str(sig),
                  "--trades-output", str(trd), "--workers", "3"])
        assert list(json.loads(sig.read_text())["tickers"]) == ["KO", "AAPL", "NVDA"]
        assert list(json.loads(trd.read_text())["trades"]) == ["KO", "AAPL", "NVDA"]
        assert set(stub) == {"KO", "AAPL", "NVDA"}


class TestProcessAll:
    def test_sequential_calls_in_order(self, monkeypatch):
        monkeypatch.setattr(gen, "build_ticker",
                            lambda t, *a, **k: (_fake_entry(t), _fake_trades()))
        seen = []
        gen._process_all(["AAPL", "NVDA", "KO"], Path("."), False, 3, 1,
                         lambda t, r: seen.append(t))
        assert seen == ["AAPL", "NVDA", "KO"]

    def test_parallel_collects_every_result(self, monkeypatch):
        monkeypatch.setattr(gen, "_pool_factory", ThreadPoolExecutor)
        monkeypatch.setattr(gen, "build_ticker",
                            lambda t, *a, **k: (_fake_entry(t), _fake_trades()))
        seen = {}
        gen._process_all(["AAPL", "NVDA", "KO", "JPM"], Path("."), False, 3, 3,
                         lambda t, r: seen.__setitem__(t, r))
        assert set(seen) == {"AAPL", "NVDA", "KO", "JPM"}
        assert all(r is not None for r in seen.values())

    def test_failing_ticker_is_isolated(self, monkeypatch):
        def fake(ticker, *a, **k):
            if ticker == "BAD":
                raise RuntimeError("boom")
            return _fake_entry(ticker), _fake_trades()

        monkeypatch.setattr(gen, "build_ticker", fake)
        seen = {}
        gen._process_all(["AAPL", "BAD", "KO"], Path("."), False, 3, 1,
                         lambda t, r: seen.__setitem__(t, r))
        assert isinstance(seen["BAD"], gen.TickerFailure)
        assert seen["BAD"].ticker == "BAD"
        assert seen["AAPL"] is not None and seen["KO"] is not None
