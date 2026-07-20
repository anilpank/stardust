"""Tests for thermaltrend/signal_store.py — signal persistence and annotation."""

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from thermaltrend.signal_store import (
    SignalRun,
    SignalStore,
    _format_runs_table,
    _format_signals_table,
)
from thermaltrend.core.events import SignalDirection, SignalEvent


def _signal(ticker="AAPL", direction=SignalDirection.BUY, strength=0.8, day=1):
    return SignalEvent(
        timestamp=datetime(2026, 1, day),
        ticker=ticker,
        direction=direction,
        strength=strength,
        strategy_id="test_strategy",
        metadata={"info": "test"},
    )


class TestSignalStore:
    def test_save_and_load(self, tmp_path):
        store = SignalStore(
            signals_dir=tmp_path / "signals",
            actions_dir=tmp_path / "actions",
        )
        signals = [_signal(), _signal("MSFT", SignalDirection.SELL, 0.6, day=2)]

        run_id = store.save(
            signals=signals,
            strategy_name="ma_crossover",
            tickers=["AAPL", "MSFT"],
            start_date="2026-01-01",
        )

        assert run_id is not None
        loaded = store.load_run(run_id)
        assert len(loaded) == 2
        assert "AAPL" in loaded["ticker"].values
        assert "MSFT" in loaded["ticker"].values

    def test_save_empty_signals(self, tmp_path):
        store = SignalStore(
            signals_dir=tmp_path / "signals",
            actions_dir=tmp_path / "actions",
        )
        run_id = store.save(
            signals=[],
            strategy_name="ma_crossover",
            tickers=["AAPL"],
        )
        loaded = store.load_run(run_id)
        assert len(loaded) == 0

    def test_list_runs(self, tmp_path):
        store = SignalStore(
            signals_dir=tmp_path / "signals",
            actions_dir=tmp_path / "actions",
        )
        store.save([_signal()], "strat_a", ["AAPL"])
        store.save([_signal()], "strat_b", ["MSFT"])

        runs = store.list_runs()
        assert len(runs) == 2
        assert runs[0].run_timestamp >= runs[1].run_timestamp  # newest first

    def test_list_runs_empty(self, tmp_path):
        store = SignalStore(
            signals_dir=tmp_path / "signals",
            actions_dir=tmp_path / "actions",
        )
        runs = store.list_runs()
        assert runs == []

    def test_load_nonexistent_run_raises(self, tmp_path):
        store = SignalStore(
            signals_dir=tmp_path / "signals",
            actions_dir=tmp_path / "actions",
        )
        with pytest.raises(FileNotFoundError):
            store.load_run("nonexistent_id")

    def test_annotate_signal(self, tmp_path):
        store = SignalStore(
            signals_dir=tmp_path / "signals",
            actions_dir=tmp_path / "actions",
        )
        run_id = store.save([_signal()], "strat", ["AAPL"])
        loaded = store.load_run(run_id)
        signal_id = loaded.iloc[0]["id"]

        store.annotate(signal_id, "acted", notes="Bought at $150")

        actions = store.get_actions([signal_id])
        assert len(actions) == 1
        assert actions.iloc[0]["action"] == "acted"
        assert actions.iloc[0]["notes"] == "Bought at $150"

    def test_annotate_overwrites_previous(self, tmp_path):
        store = SignalStore(
            signals_dir=tmp_path / "signals",
            actions_dir=tmp_path / "actions",
        )
        run_id = store.save([_signal()], "strat", ["AAPL"])
        loaded = store.load_run(run_id)
        signal_id = loaded.iloc[0]["id"]

        store.annotate(signal_id, "acted")
        store.annotate(signal_id, "skipped", notes="Changed mind")

        actions = store.get_actions([signal_id])
        assert len(actions) == 1
        assert actions.iloc[0]["action"] == "skipped"

    def test_annotate_invalid_action_raises(self, tmp_path):
        store = SignalStore(
            signals_dir=tmp_path / "signals",
            actions_dir=tmp_path / "actions",
        )
        with pytest.raises(ValueError, match="action must be"):
            store.annotate("fake_id", "invalid_action")

    def test_get_pending_signals(self, tmp_path):
        store = SignalStore(
            signals_dir=tmp_path / "signals",
            actions_dir=tmp_path / "actions",
        )
        sig1 = _signal("AAPL", day=1)
        sig2 = _signal("MSFT", day=2)
        store.save([sig1, sig2], "strat", ["AAPL", "MSFT"])

        loaded = store.load_run(store.list_runs()[0].run_id)
        store.annotate(loaded.iloc[0]["id"], "acted")  # annotate AAPL

        pending = store.get_pending_signals()
        assert len(pending) == 1
        assert pending.iloc[0]["ticker"] == "MSFT"

    def test_load_all_signals_filters(self, tmp_path):
        store = SignalStore(
            signals_dir=tmp_path / "signals",
            actions_dir=tmp_path / "actions",
        )
        store.save([_signal("AAPL", day=1)], "strat_a", ["AAPL"])
        store.save([_signal("MSFT", day=2)], "strat_b", ["MSFT"])

        all_signals = store.load_all_signals()
        assert len(all_signals) == 2

        filtered = store.load_all_signals(ticker="AAPL")
        assert len(filtered) == 1
        assert filtered.iloc[0]["ticker"] == "AAPL"

        filtered = store.load_all_signals(strategy="strat_b")
        assert len(filtered) == 1

    def test_load_all_signals_date_range(self, tmp_path):
        store = SignalStore(
            signals_dir=tmp_path / "signals",
            actions_dir=tmp_path / "actions",
        )
        store.save([_signal("AAPL", day=1)], "strat", ["AAPL"])
        store.save([_signal("AAPL", day=5)], "strat", ["AAPL"])

        filtered = store.load_all_signals(
            from_date="2026-01-03",
            to_date="2026-01-10",
        )
        assert len(filtered) == 1


class TestFormatTables:
    def test_format_runs_table_empty(self):
        result = _format_runs_table([])
        assert "No saved runs" in result

    def test_format_runs_table(self):
        runs = [
            SignalRun(
                run_id="20260101_120000_abc123",
                strategy_name="ma_crossover",
                tickers=["AAPL", "MSFT"],
                start_date="2026-01-01",
                end_date=None,
                params={},
                run_timestamp=datetime(2026, 1, 1, 12, 0),
                signal_count=5,
            ),
        ]
        result = _format_runs_table(runs)
        assert "ma_crossover" in result
        assert "AAPL" in result
        assert "5" in result

    def test_format_signals_table_empty(self):
        result = _format_signals_table(pd.DataFrame())
        assert "No signals" in result

    def test_format_signals_table(self):
        df = pd.DataFrame([{
            "id": "abc-123",
            "timestamp": datetime(2026, 1, 1),
            "ticker": "AAPL",
            "direction": "BUY",
            "strength": 0.85,
            "strategy_name": "ma_crossover",
        }])
        result = _format_signals_table(df)
        assert "AAPL" in result
        assert "BUY" in result
        assert "0.8500" in result
