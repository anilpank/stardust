"""Tests for thermaltrend/backtest.py — single-strategy backtest CLI."""

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from thermaltrend.backtest import STRATEGIES, _print_summary, run_backtest
from thermaltrend.core.events import SignalDirection, SignalEvent


def _make_parquet(tmp_path, ticker, dates_closes):
    df = pd.DataFrame(
        {"Open": [c for _, c in dates_closes],
         "High": [c + 1 for _, c in dates_closes],
         "Low": [c - 1 for _, c in dates_closes],
         "Close": [c for _, c in dates_closes],
         "Volume": [1000] * len(dates_closes),
         "ticker": ticker},
        index=pd.DatetimeIndex([d for d, _ in dates_closes], name="Date"),
    )
    df.to_parquet(tmp_path / f"{ticker}.parquet")


class TestRunBacktest:
    def test_single_ticker(self, tmp_path):
        dates = pd.bdate_range("2026-01-01", periods=30)
        closes = [100 + i * 0.5 for i in range(30)]
        _make_parquet(tmp_path, "TEST", list(zip(dates, closes)))

        result = run_backtest(
            strategy_name="ma_crossover",
            tickers=["TEST"],
            start_date="2026-01-01",
            data_dir=str(tmp_path),
        )

        assert "metrics" in result
        assert "trades" in result
        assert "confidence" in result
        assert result["strategy_name"] == "ma_crossover"

    def test_unknown_strategy_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown strategy"):
            run_backtest("nonexistent", ["TEST"], data_dir=str(tmp_path))

    def test_empty_data_raises(self, tmp_path):
        with pytest.raises(ValueError, match="No data found"):
            run_backtest(
                "ma_crossover", ["NONEXISTENT"],
                start_date="2026-01-01",
                data_dir=str(tmp_path),
            )

    def test_custom_params(self, tmp_path):
        dates = pd.bdate_range("2026-01-01", periods=30)
        closes = [100 + i * 0.5 for i in range(30)]
        _make_parquet(tmp_path, "TEST", list(zip(dates, closes)))

        result = run_backtest(
            strategy_name="ma_crossover",
            tickers=["TEST"],
            start_date="2026-01-01",
            params={"fast_period": 3, "slow_period": 5},
            data_dir=str(tmp_path),
        )

        assert result["metrics"]["total_trades"] >= 0

    def test_multiple_tickers(self, tmp_path):
        dates = pd.bdate_range("2026-01-01", periods=30)
        closes_a = [100 + i * 0.5 for i in range(30)]
        closes_b = [200 + i * 0.3 for i in range(30)]
        _make_parquet(tmp_path, "A", list(zip(dates, closes_a)))
        _make_parquet(tmp_path, "B", list(zip(dates, closes_b)))

        result = run_backtest(
            strategy_name="donchian",
            tickers=["A", "B"],
            start_date="2026-01-01",
            data_dir=str(tmp_path),
        )

        assert "metrics" in result

    def test_all_strategies_run(self, tmp_path):
        dates = pd.bdate_range("2026-01-01", periods=50)
        closes = [100 + i * (1 if i % 10 < 5 else -1) for i in range(50)]
        _make_parquet(tmp_path, "TEST", list(zip(dates, closes)))

        for name in STRATEGIES:
            result = run_backtest(
                strategy_name=name,
                tickers=["TEST"],
                start_date="2026-01-01",
                data_dir=str(tmp_path),
            )
            assert "metrics" in result, f"Strategy {name} failed"


class TestPrintSummary:
    def _make_result(self):
        return {
            "strategy_name": "test_strategy",
            "trades": [],
            "equity_curve": pd.Series([100000.0]),
            "per_ticker": {},
            "metrics": {
                "cagr": 0.1234,
                "sharpe": 1.5,
                "sortino": 2.1,
                "max_drawdown": -0.15,
                "calmar": 0.82,
                "win_rate": 0.6,
                "profit_factor": 1.8,
                "avg_trade_pnl": 150.0,
                "avg_holding_days": 12.5,
                "total_trades": 25,
                "trades_completed": 23,
                "trades_open": 2,
                "total_return": 3750.0,
            },
            "confidence": 0.75,
        }

    def test_prints_metrics(self, tmp_path, capsys):
        result = self._make_result()
        _print_summary(result, show_per_ticker=False, show_regime=False)
        captured = capsys.readouterr()
        assert "test_strategy" in captured.out
        assert "12.3%" in captured.out
        assert "1.50" in captured.out

    def test_per_ticker_flag(self, capsys):
        result = self._make_result()
        result["per_ticker"] = {
            "TEST": {
                "total_trades": 5,
                "trades_completed": 5,
                "win_rate": 0.6,
                "profit_factor": 1.5,
                "avg_trade_pnl": 100.0,
                "total_pnl": 500.0,
                "avg_holding_days": 10.0,
            }
        }
        _print_summary(result, show_per_ticker=True, show_regime=False)
        captured = capsys.readouterr()
        assert "Per-Ticker" in captured.out
