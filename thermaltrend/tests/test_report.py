"""Tests for thermaltrend.analytics.report."""

import json
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from thermaltrend.analytics.report import (
    format_cross_strategy_period_table,
    format_per_ticker_table,
    format_period_table,
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


class TestFormatPeriodTable:
    def test_basic(self):
        period_metrics = {
            "2026-01": {
                "total_trades": 10,
                "trades_completed": 8,
                "win_rate": 0.625,
                "profit_factor": 1.8,
                "avg_trade_pnl": 75.0,
                "total_pnl": 600.0,
                "avg_holding_days": 5.0,
            },
            "2026-02": {
                "total_trades": 5,
                "trades_completed": 5,
                "win_rate": 0.4,
                "profit_factor": 1.1,
                "avg_trade_pnl": -20.0,
                "total_pnl": -100.0,
                "avg_holding_days": 3.0,
            },
        }
        table = format_period_table(period_metrics, "my_strategy", "monthly")
        assert "2026-01" in table
        assert "2026-02" in table
        assert "my_strategy" in table
        assert "Monthly" in table
        assert "TOTAL" in table

    def test_empty(self):
        table = format_period_table({}, "test", "monthly")
        assert "No period data" in table

    def test_yearly(self):
        period_metrics = {
            "2025": {"total_trades": 20, "trades_completed": 18, "win_rate": 0.6, "total_pnl": 2000.0},
            "2026": {"total_trades": 10, "trades_completed": 10, "win_rate": 0.5, "total_pnl": -500.0},
        }
        table = format_period_table(period_metrics, "test", "yearly")
        assert "Yearly" in table
        assert "2025" in table
        assert "2026" in table


class TestFormatCrossStrategyPeriodTable:
    def test_basic(self):
        strategy_results = {
            "MA 50/200": {
                "trades": [
                    _make_trade(pnl=500.0, pnl_pct=0.05),
                ],
            },
            "Donchian": {
                "trades": [
                    _make_trade(pnl=800.0, pnl_pct=0.08),
                ],
            },
        }
        table = format_cross_strategy_period_table(strategy_results, "monthly")
        assert "MA 50/200" in table
        assert "Donchian" in table
        assert "TOTAL" in table
        assert "*" in table

    def test_empty(self):
        table = format_cross_strategy_period_table({}, "monthly")
        assert "No strategy results" in table
