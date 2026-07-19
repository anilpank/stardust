"""Tests for thermaltrend.analytics.regime."""

import numpy as np
import pandas as pd
import pytest

from thermaltrend.analytics.regime import (
    MarketRegime,
    classify_regime,
    compute_regime_metrics,
)
from thermaltrend.analytics.trade_simulator import Trade
from thermaltrend.core.events import SignalDirection
from datetime import datetime


def _make_trade(ticker="TEST", entry_date=datetime(2026, 1, 5), pnl=100.0, pnl_pct=0.01):
    shares = int(10_000 / 100.0)
    return Trade(
        ticker=ticker,
        entry_date=entry_date,
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


class TestClassifyRegime:
    def test_uptrend_is_bull(self):
        dates = pd.bdate_range("2020-01-01", periods=300)
        closes = pd.Series(np.linspace(3000, 4000, 300), index=dates)
        regimes = classify_regime(closes)
        last_regime = regimes.iloc[-1]
        assert last_regime == MarketRegime.BULL

    def test_downtrend_is_bear(self):
        dates = pd.bdate_range("2020-01-01", periods=300)
        closes = pd.Series(np.linspace(4000, 3000, 300), index=dates)
        regimes = classify_regime(closes)
        last_regime = regimes.iloc[-1]
        assert last_regime == MarketRegime.BEAR

    def test_flat_is_sideways(self):
        dates = pd.bdate_range("2020-01-01", periods=300)
        closes = pd.Series(np.full(300, 4000.0), index=dates)
        regimes = classify_regime(closes)
        last_regime = regimes.iloc[-1]
        assert last_regime == MarketRegime.SIDEWAYS

    def test_output_matches_input_length(self):
        dates = pd.bdate_range("2020-01-01", periods=100)
        closes = pd.Series(np.linspace(3000, 3500, 100), index=dates)
        regimes = classify_regime(closes)
        assert len(regimes) == len(closes)


class TestComputeRegimeMetrics:
    def test_empty_trades(self):
        dates = pd.bdate_range("2026-01-01", periods=10)
        regimes = pd.Series(MarketRegime.BULL, index=dates)
        result = compute_regime_metrics([], regimes)
        assert result["bull"]["total_trades"] == 0

    def test_trades_in_bull_regime(self):
        dates = pd.bdate_range("2026-01-01", periods=10)
        regimes = pd.Series(MarketRegime.BULL, index=dates)
        trades = [_make_trade(entry_date=datetime(2026, 1, 5))]
        result = compute_regime_metrics(trades, regimes)
        assert result["bull"]["total_trades"] == 1
        assert result["bear"]["total_trades"] == 0

    def test_trades_split_across_regimes(self):
        dates = pd.bdate_range("2026-01-01", periods=10)
        regimes = pd.Series(
            [MarketRegime.BULL] * 5 + [MarketRegime.BEAR] * 5, index=dates
        )
        trades = [
            _make_trade(entry_date=datetime(2026, 1, 2)),
            _make_trade(entry_date=datetime(2026, 1, 8)),
        ]
        result = compute_regime_metrics(trades, regimes)
        assert result["bull"]["total_trades"] == 1
        assert result["bear"]["total_trades"] == 1
