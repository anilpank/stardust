"""Tests for thermaltrend.analytics.metrics."""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from thermaltrend.analytics.trade_simulator import Trade
from thermaltrend.analytics.metrics import (
    compute_aggregate_metrics,
    compute_benchmark_metrics,
    compute_confidence,
    compute_equity_curve,
    compute_per_ticker_metrics,
)
from thermaltrend.core.events import SignalDirection


def _make_trade(
    ticker="TEST",
    entry_date=datetime(2026, 1, 5),
    entry_price=100.0,
    exit_date=datetime(2026, 1, 15),
    exit_price=110.0,
    pnl=None,
    pnl_pct=None,
    holding_days=10,
    exit_reason="signal",
    strategy_id="test",
):
    if pnl is None:
        shares = int(10_000 / entry_price)
        pnl = (exit_price - entry_price) * shares
    if pnl_pct is None:
        pnl_pct = (exit_price - entry_price) / entry_price
    return Trade(
        ticker=ticker,
        entry_date=entry_date,
        entry_price=entry_price,
        exit_date=exit_date,
        exit_price=exit_price,
        direction=SignalDirection.BUY,
        pnl=pnl,
        pnl_pct=pnl_pct,
        holding_days=holding_days,
        exit_reason=exit_reason,
        strategy_id=strategy_id,
        shares=int(10_000 / entry_price),
    )


class TestComputeEquityCurve:
    def test_empty_trades(self):
        curve = compute_equity_curve([], pd.Timestamp("2026-01-01"))
        assert len(curve) == 1
        assert curve.iloc[0] == 100_000.0

    def test_single_winning_trade(self):
        trade = _make_trade(
            entry_date=datetime(2026, 1, 5),
            exit_date=datetime(2026, 1, 15),
            pnl=500.0,
        )
        curve = compute_equity_curve([trade], pd.Timestamp("2026-01-01"))
        assert curve.iloc[0] == 100_000.0
        assert curve.iloc[-1] >= 100_000.0

    def test_data_end_trades_excluded(self):
        trade = _make_trade(
            entry_date=datetime(2026, 1, 5),
            exit_date=datetime(2026, 1, 15),
            pnl=500.0,
            exit_reason="data_end",
        )
        curve = compute_equity_curve([trade], pd.Timestamp("2026-01-01"))
        assert curve.iloc[-1] == 100_000.0


class TestComputeAggregateMetrics:
    def test_no_trades(self):
        metrics = compute_aggregate_metrics([])
        assert metrics["total_trades"] == 0
        assert metrics["trades_completed"] == 0
        assert metrics["cagr"] == 0.0
        assert metrics["win_rate"] == 0.0

    def test_all_winning_trades(self):
        trades = [
            _make_trade(
                entry_date=datetime(2026, 1, 5),
                exit_date=datetime(2026, 1, 15),
                exit_price=110.0,
            ),
            _make_trade(
                entry_date=datetime(2026, 1, 20),
                exit_date=datetime(2026, 1, 30),
                exit_price=120.0,
            ),
        ]
        metrics = compute_aggregate_metrics(trades)
        assert metrics["win_rate"] == 1.0
        assert metrics["total_trades"] == 2
        assert metrics["trades_completed"] == 2
        assert metrics["avg_trade_pnl"] > 0

    def test_mixed_trades(self):
        trades = [
            _make_trade(exit_price=110.0, pnl=500.0, pnl_pct=0.10),
            _make_trade(exit_price=90.0, pnl=-500.0, pnl_pct=-0.10),
        ]
        metrics = compute_aggregate_metrics(trades)
        assert metrics["win_rate"] == 0.5
        assert metrics["total_trades"] == 2

    def test_open_trades_counted(self):
        trades = [
            _make_trade(exit_reason="signal"),
            _make_trade(exit_reason="data_end"),
        ]
        metrics = compute_aggregate_metrics(trades)
        assert metrics["total_trades"] == 2
        assert metrics["trades_completed"] == 1
        assert metrics["trades_open"] == 1

    def test_profit_factor(self):
        trades = [
            _make_trade(exit_price=110.0, pnl=1000.0, pnl_pct=0.10),
            _make_trade(exit_price=95.0, pnl=-500.0, pnl_pct=-0.05),
        ]
        metrics = compute_aggregate_metrics(trades)
        assert metrics["profit_factor"] == pytest.approx(2.0, rel=1e-3)

    def test_with_equity_curve(self):
        trades = [
            _make_trade(
                entry_date=datetime(2026, 1, 5),
                exit_date=datetime(2026, 1, 15),
                pnl=500.0,
            ),
        ]
        curve = compute_equity_curve(trades, pd.Timestamp("2026-01-01"))
        metrics = compute_aggregate_metrics(trades, curve)
        assert "cagr" in metrics
        assert "sharpe" in metrics
        assert "sortino" in metrics
        assert "max_drawdown" in metrics


class TestComputePerTickerMetrics:
    def test_empty(self):
        result = compute_per_ticker_metrics([])
        assert result == {}

    def test_single_ticker(self):
        trades = [
            _make_trade(ticker="AAPL", exit_price=110.0, pnl=500.0),
            _make_trade(ticker="AAPL", exit_price=120.0, pnl=1000.0),
        ]
        result = compute_per_ticker_metrics(trades)
        assert "AAPL" in result
        assert result["AAPL"]["total_trades"] == 2
        assert result["AAPL"]["win_rate"] == 1.0

    def test_multiple_tickers(self):
        trades = [
            _make_trade(ticker="AAPL", exit_price=110.0),
            _make_trade(ticker="MSFT", exit_price=90.0),
        ]
        result = compute_per_ticker_metrics(trades)
        assert "AAPL" in result
        assert "MSFT" in result

    def test_ticker_with_only_open_trades(self):
        trades = [_make_trade(ticker="AAPL", exit_reason="data_end")]
        result = compute_per_ticker_metrics(trades)
        assert result["AAPL"]["status"] == "no_completed_trades"


class TestComputeConfidence:
    def test_no_trades(self):
        assert compute_confidence([]) == 0.0

    def test_few_trades_low_confidence(self):
        trades = [_make_trade() for _ in range(5)]
        conf = compute_confidence(trades)
        assert 0.0 <= conf <= 0.5

    def test_many_trades_higher_confidence(self):
        trades = [_make_trade(exit_price=100 + i, pnl=i * 10, pnl_pct=i / 100) for i in range(50)]
        conf = compute_confidence(trades)
        assert conf > 0.3

    def test_confidence_bounded(self):
        trades = [_make_trade(exit_price=100 + i) for i in range(100)]
        conf = compute_confidence(trades)
        assert 0.0 <= conf <= 1.0


class TestComputeBenchmarkMetrics:
    def test_basic(self):
        dates = pd.bdate_range("2020-01-01", periods=500)
        closes = np.linspace(300, 500, 500)
        spy_data = pd.DataFrame({"Open": closes, "Close": closes}, index=dates)

        result = compute_benchmark_metrics(spy_data, "2020-01-01", "2021-12-31")
        assert "cagr" in result
        assert "sharpe" in result
        assert result["cagr"] > 0  # uptrend

    def test_empty_data(self):
        spy_data = pd.DataFrame(columns=["Open", "Close"])
        result = compute_benchmark_metrics(spy_data, "2020-01-01", "2021-01-01")
        assert result["cagr"] == 0.0
