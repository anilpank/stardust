"""Tests for thermaltrend.analytics.compare."""

import pandas as pd
import pytest

from thermaltrend.analytics.compare import compare_strategies, run_strategy_analysis
from thermaltrend.analytics.trade_simulator import Trade
from thermaltrend.core.events import SignalDirection, SignalEvent
from datetime import datetime


def _make_trade(ticker="TEST", pnl=100.0, pnl_pct=0.01, exit_reason="signal"):
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
        exit_reason=exit_reason,
        strategy_id="test",
        shares=shares,
    )


class TestCompareStrategies:
    def test_basic_comparison(self):
        strategy_results = {
            "strategy_a": {"trades": [_make_trade(pnl=500)]},
            "strategy_b": {"trades": [_make_trade(pnl=300)]},
        }
        df = compare_strategies(strategy_results)
        assert len(df) == 2
        assert "strategy" in df.columns
        assert "cagr" in df.columns
        assert "sharpe" in df.columns

    def test_with_benchmark(self):
        strategy_results = {
            "my_strat": {"trades": [_make_trade(pnl=500)]},
        }
        bench = {"cagr": 0.10, "sharpe": 0.5, "max_drawdown": -0.20}
        df = compare_strategies(strategy_results, benchmark_metrics=bench)
        assert len(df) == 2
        assert "S&P 500 B&H" in df["strategy"].values

    def test_sort_by_total_trades(self):
        strategy_results = {
            "few_trades": {"trades": [_make_trade()] * 3},
            "many_trades": {"trades": [_make_trade()] * 10},
        }
        df = compare_strategies(strategy_results, sort_by="total_trades")
        assert df.iloc[0]["strategy"] == "many_trades"
        assert df.iloc[0]["total_trades"] == 10

    def test_confidence_included(self):
        strategy_results = {
            "my_strat": {"trades": [_make_trade() for _ in range(30)]},
        }
        df = compare_strategies(strategy_results)
        assert "confidence" in df.columns
        assert df.iloc[0]["confidence"] > 0

    def test_ranking_index_starts_at_1(self):
        strategy_results = {
            "a": {"trades": [_make_trade()]},
            "b": {"trades": [_make_trade()]},
        }
        df = compare_strategies(strategy_results)
        assert df.index[0] == 1


class TestRunStrategyAnalysis:
    def test_returns_all_keys(self):
        dates = pd.bdate_range("2026-01-01", periods=20)
        opens = [100.0 + i for i in range(20)]
        closes = [100.5 + i for i in range(20)]

        idx = pd.MultiIndex.from_arrays(
            [dates, ["TEST"] * 20], names=["date", "ticker"]
        )
        price_data = pd.DataFrame(
            {"Open": opens, "High": [o + 1 for o in opens],
             "Low": [o - 1 for o in opens], "Close": closes, "Volume": [10000] * 20},
            index=idx,
        )

        signals = [
            SignalEvent(
                timestamp=dates[2].to_pydatetime(),
                ticker="TEST",
                direction=SignalDirection.BUY,
                strength=0.8,
                strategy_id="test",
            ),
            SignalEvent(
                timestamp=dates[10].to_pydatetime(),
                ticker="TEST",
                direction=SignalDirection.SELL,
                strength=0.8,
                strategy_id="test",
            ),
        ]

        result = run_strategy_analysis(signals, price_data, "test_strategy")
        assert "strategy_name" in result
        assert "trades" in result
        assert "equity_curve" in result
        assert "per_ticker" in result
        assert "metrics" in result
        assert "confidence" in result
        assert result["strategy_name"] == "test_strategy"
