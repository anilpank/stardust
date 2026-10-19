"""Tests for landing/generate_portfolio_data.py — the portfolio-console payload."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from landing import generate_portfolio_data as gen


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
    dates = pd.bdate_range("2025-01-02", periods=80)
    wave = 100.0 + 10.0 * np.sin(np.arange(80) / 5.0)
    _make_parquet(tmp_path, "TEST", dates, [float(c) for c in wave])
    _make_parquet(
        tmp_path, "SPY", dates, [float(c) for c in 400.0 + 20.0 * np.sin(np.arange(80) / 5.0)]
    )
    return tmp_path


def _runs():
    return [
        {
            "id": "r1",
            "label": "Run 1",
            "strategy": "ma_crossover",
            "tickers": ["TEST"],
            "capital": 10_000.0,
        }
    ]


class TestJsonable:
    def test_numpy_and_timestamps(self):
        out = gen._jsonable(
            {"a": np.float64(1.5), "b": pd.Timestamp("2026-01-02"), "c": [np.int64(3)]}
        )
        assert out == {"a": 1.5, "b": "2026-01-02T00:00:00", "c": [3]}

    def test_nan_becomes_none(self):
        assert gen._jsonable({"x": float("nan")}) == {"x": None}

    def test_enum_value(self):
        from thermaltrend.core.events import SignalDirection

        assert gen._jsonable(SignalDirection.BUY) == "BUY"


class TestBuildPayload:
    def test_run_shape(self, data_dir):
        payload = gen.build_payload(runs=_runs(), data_dir=data_dir)
        assert set(payload) == {"generated_at", "runs"}
        run = payload["runs"][0]
        for key in (
            "id", "label", "strategy", "tickers", "capital", "config", "metrics",
            "equity", "daily", "trades", "ratchet_events", "breaker_events",
            "rejected_counts",
        ):
            assert key in run, key
        assert len(run["equity"]["dates"]) == len(run["equity"]["portfolio"])
        assert len(run["equity"]["dates"]) == len(run["equity"]["spy"])
        assert len(run["daily"]["dates"]) == len(run["daily"]["exposure"])
        assert len(run["daily"]["dates"]) == len(run["daily"]["breaker"])
        assert len(run["daily"]["dates"]) == len(run["daily"]["core"])
        assert all(c in ("", "b", "r", "s") for c in run["daily"]["regime"])
        json.dumps(payload)

    def test_metrics_have_safety_fields(self, data_dir):
        payload = gen.build_payload(runs=_runs(), data_dir=data_dir)
        m = payload["runs"][0]["metrics"]
        for key in ("worst_12m", "breaker_days_pct", "band_violations", "banked_total", "benchmark"):
            assert key in m

    def test_spy_line_is_rebased_to_capital(self, data_dir):
        payload = gen.build_payload(runs=_runs(), data_dir=data_dir)
        spy = payload["runs"][0]["equity"]["spy"]
        first = next(v for v in spy if v is not None)
        assert first == pytest.approx(10_000.0)

    def test_default_out_path_is_private_dir(self):
        assert gen.DEFAULT_OUT.parent.name == "private"
        assert gen.DEFAULT_OUT.name == "portfolio_data.json"

    def test_default_runs_include_both_controls(self):
        ids = {r["id"] for r in gen.DEFAULT_RUNS}
        assert "core-only-spy-10k" in ids
        assert "satellite-only-aapl-msft-10k" in ids
        core_only = next(r for r in gen.DEFAULT_RUNS if r["id"] == "core-only-spy-10k")
        assert core_only["satellite"] is False

    def test_default_runs_pair_yield_off_and_tbill(self):
        from thermaltrend.analytics.metrics import RISK_FREE_RATE

        by_id = {r["id"]: r for r in gen.DEFAULT_RUNS}
        for base, tbill in (
            ("ma-crossover-aapl-msft-10k", "ma-crossover-aapl-msft-10k-tbill"),
            ("core-only-spy-10k", "core-only-spy-10k-tbill"),
            ("satellite-only-aapl-msft-10k", "satellite-only-aapl-msft-10k-tbill"),
        ):
            assert tbill in by_id
            assert by_id[base].get("config") is None or by_id[base]["config"].cash_yield == 0.0
            assert by_id[tbill]["config"].cash_yield == RISK_FREE_RATE
        sat = by_id["satellite-only-aapl-msft-10k-tbill"]["config"]
        assert sat.core_enabled is False and sat.cash_yield == RISK_FREE_RATE

    def test_default_runs_include_point_in_time_validation(self):
        by_id = {r["id"]: r for r in gen.DEFAULT_RUNS}
        for rid in (
            "pit-full-ma-crossover-10k-tbill",
            "pit-satellite-only-ma-crossover-10k-tbill",
            "pit-full-donchian-10k-tbill",
            "pit-satellite-only-donchian-10k-tbill",
        ):
            assert rid in by_id
            assert by_id[rid]["universe"] == "point_in_time"
            assert not by_id[rid].get("tickers")
        assert (
            by_id["pit-satellite-only-ma-crossover-10k-tbill"]["config"].core_enabled
            is False
        )

    def test_point_in_time_run_uses_membership_tickers(self, data_dir, monkeypatch):
        seen = {}
        real = gen.run_portfolio_backtest

        def spy(**kwargs):
            seen.update(kwargs)
            return real(**kwargs)

        monkeypatch.setattr(gen, "run_portfolio_backtest", spy)
        monkeypatch.setattr(gen, "_membership_tickers", lambda: ["TEST"])
        runs = [
            {
                "id": "pit",
                "label": "PIT",
                "strategy": "ma_crossover",
                "capital": 10_000.0,
                "start": "2025-01-02",
                "universe": "point_in_time",
            }
        ]
        gen.build_payload(runs=runs, data_dir=data_dir)
        assert seen["universe"] == "point_in_time"
        assert seen["tickers"] == ["TEST"]

    def test_core_only_spec_serializes_without_strategy(self, data_dir):
        runs = [{"id": "c", "label": "Core only", "satellite": False, "capital": 10_000.0}]
        payload = gen.build_payload(runs=runs, data_dir=data_dir)
        run = payload["runs"][0]
        assert run["strategy"] is None
        assert run["tickers"] == []
        assert all(t["lane"] == "core" for t in run["trades"])


class TestSavePayload:
    def test_round_trip(self, data_dir, tmp_path):
        payload = gen.build_payload(runs=_runs(), data_dir=data_dir)
        path = gen.save_payload(payload, tmp_path / "out.json")
        assert json.loads(path.read_text(encoding="utf-8"))["runs"][0]["id"] == "r1"

    def test_creates_parent_dirs(self, tmp_path):
        payload = {"generated_at": "x", "runs": []}
        path = gen.save_payload(payload, tmp_path / "a" / "b" / "out.json")
        assert path.exists()
