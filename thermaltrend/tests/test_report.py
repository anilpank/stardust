"""Tests for thermaltrend.analytics.report."""

import json
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from thermaltrend.analytics.report import (
    format_per_ticker_table,
    format_ranking_table,
    format_regime_table,
    format_signals_table,
    export_json,
    export_trades_csv,
)
from thermaltrend.analytics.trade_simulator import Trade
from thermaltrend.core.events import SignalDirection, SignalEvent
from datetime import datetime


def _make_trade(ticker="TEST", pnl=100.0, pnl_pct=0.01):
    shares = int(10_000 / 100.0)
    return Trade(
        ticker=ticker,
        entry_date=datetime(2026, 1, 5),
        entry_price=100.0,
        exit_date=datetime(2026, 1, 15),
        exit_price=100.0 + pnl / shares,
        direction=SignalDirection.BUY,
        pnl=pnl,
        pnl_pct=pnl_pct,
        holding_days=10,
        exit_reason="signal",
        strategy_id="test",
        shares=shares,
    )


class TestFormatRankingTable:
    def test_basic(self):
        df = pd.DataFrame({
            "strategy": ["ma_crossover", "donchian"],
            "cagr": [0.12, 0.09],
            "sharpe": [0.85, 0.62],
            "sortino": [1.2, 0.9],
            "max_drawdown": [-0.18, -0.22],
            "calmar": [0.67, 0.41],
            "win_rate": [0.58, 0.51],
            "profit_factor": [1.5, 1.2],
            "total_trades": [142, 98],
            "confidence": [0.82, 0.71],
        })
        table = format_ranking_table(df)
        assert "ma_crossover" in table
        assert "donchian" in table
        assert "12.0%" in table

    def test_empty(self):
        df = pd.DataFrame()
        table = format_ranking_table(df)
        assert "No strategies" in table


class TestFormatPerTickerTable:
    def test_basic(self):
        per_ticker = {
            "AAPL": {
                "trades_completed": 10,
                "win_rate": 0.6,
                "profit_factor": 1.5,
                "avg_trade_pnl": 50.0,
                "total_pnl": 500.0,
                "avg_holding_days": 5.0,
            },
            "MSFT": {
                "trades_completed": 8,
                "win_rate": 0.5,
                "profit_factor": 1.2,
                "avg_trade_pnl": 30.0,
                "total_pnl": 240.0,
                "avg_holding_days": 4.0,
            },
        }
        table = format_per_ticker_table(per_ticker, "my_strategy")
        assert "AAPL" in table
        assert "MSFT" in table
        assert "my_strategy" in table

    def test_empty(self):
        table = format_per_ticker_table({}, "test")
        assert "No per-ticker data" in table


class TestFormatRegimeTable:
    def test_basic(self):
        regime_metrics = {
            "bull": {"total_trades": 10, "win_rate": 0.7, "avg_trade_pnl": 100.0, "total_pnl": 1000.0, "avg_holding_days": 5.0},
            "bear": {"total_trades": 5, "win_rate": 0.4, "avg_trade_pnl": -50.0, "total_pnl": -250.0, "avg_holding_days": 3.0},
            "sideways": {"total_trades": 0},
        }
        table = format_regime_table(regime_metrics, "my_strategy")
        assert "BULL" in table
        assert "BEAR" in table
        assert "my_strategy" in table


class TestFormatSignalsTable:
    def test_basic(self):
        signals = [
            SignalEvent(
                timestamp=datetime(2026, 1, 5),
                ticker="AAPL",
                direction=SignalDirection.BUY,
                strength=0.8,
                strategy_id="test",
            ),
        ]
        table = format_signals_table(signals, "test_strategy")
        assert "AAPL" in table
        assert "BUY" in table

    def test_empty(self):
        table = format_signals_table([], "test")
        assert "No signals" in table


class TestExportJson:
    def test_basic(self):
        results = {
            "strategy_name": "test",
            "metrics": {"cagr": 0.12, "sharpe": 0.85},
            "confidence": 0.75,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "results.json"
            export_json(results, path)
            assert path.exists()
            with open(path) as f:
                data = json.load(f)
            assert data["strategy_name"] == "test"
            assert data["metrics"]["cagr"] == 0.12

    def test_with_trades(self):
        trade = _make_trade()
        results = {"trades": [trade]}
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "results.json"
            export_json(results, path)
            with open(path) as f:
                data = json.load(f)
            assert len(data["trades"]) == 1
            assert data["trades"][0]["ticker"] == "TEST"


class TestExportTradesCsv:
    def test_basic(self):
        trades = [_make_trade(ticker="AAPL"), _make_trade(ticker="MSFT")]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "trades.csv"
            export_trades_csv(trades, path)
            assert path.exists()
            df = pd.read_csv(path)
            assert len(df) == 2
            assert "ticker" in df.columns
            assert "pnl" in df.columns

    def test_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "trades.csv"
            export_trades_csv([], path)
            assert not path.exists()
