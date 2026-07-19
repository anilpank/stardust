"""Tests for thermaltrend.analytics.trade_simulator."""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from thermaltrend.analytics.trade_simulator import Trade, TradeSimulator
from thermaltrend.core.events import SignalDirection, SignalEvent


def _make_signal(ticker, date, direction, strategy_id="test_strat"):
    return SignalEvent(
        timestamp=date,
        ticker=ticker,
        direction=direction,
        strength=0.8,
        strategy_id=strategy_id,
    )


def _make_price_data(ticker, dates, opens, closes, highs=None, lows=None):
    """Build a MultiIndex DataFrame mimicking DataFeed._data."""
    if highs is None:
        highs = [o + 2 for o in opens]
    if lows is None:
        lows = [o - 1 for o in opens]

    df = pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": [100000] * len(dates),
    }, index=pd.DatetimeIndex(dates, name="date"))

    df["ticker"] = ticker
    df = df.set_index("ticker", append=True)
    df.index.names = ["date", "ticker"]
    return df


def _simple_up_data(ticker="TEST", start="2026-01-01", n=30):
    """Create simple uptrending price data."""
    dates = pd.bdate_range(start, periods=n)
    opens = [100.0 + i * 0.5 for i in range(n)]
    closes = [100.5 + i * 0.5 for i in range(n)]
    return _make_price_data(ticker, dates, opens, closes), dates


def _simple_down_data(ticker="TEST", start="2026-01-01", n=30):
    """Create simple downtrending price data."""
    dates = pd.bdate_range(start, periods=n)
    opens = [150.0 - i * 0.5 for i in range(n)]
    closes = [149.5 - i * 0.5 for i in range(n)]
    return _make_price_data(ticker, dates, opens, closes), dates


class TestTradeSimulator:
    def test_empty_signals(self):
        sim = TradeSimulator()
        price_data, _ = _simple_up_data()
        trades = sim.simulate([], price_data)
        assert trades == []

    def test_empty_price_data(self):
        sim = TradeSimulator()
        signals = [_make_signal("TEST", datetime(2026, 1, 5), SignalDirection.BUY)]
        trades = sim.simulate(signals, pd.DataFrame())
        assert trades == []

    def test_basic_buy_sell_cycle(self):
        sim = TradeSimulator(stop_atr_multiple=0.0)
        price_data, dates = _simple_up_data(n=20)

        signals = [
            _make_signal("TEST", dates[2], SignalDirection.BUY),
            _make_signal("TEST", dates[10], SignalDirection.SELL),
        ]
        trades = sim.simulate(signals, price_data)
        assert len(trades) == 1
        trade = trades[0]
        assert trade.ticker == "TEST"
        assert trade.direction == SignalDirection.BUY
        assert trade.exit_reason == "signal"
        assert trade.pnl > 0  # uptrend = profit
        assert trade.holding_days > 0
        assert trade.shares > 0

    def test_entry_at_next_day_open(self):
        sim = TradeSimulator(stop_atr_multiple=0.0)
        price_data, dates = _simple_up_data(n=20)

        signals = [
            _make_signal("TEST", dates[2], SignalDirection.BUY),
            _make_signal("TEST", dates[10], SignalDirection.SELL),
        ]
        trades = sim.simulate(signals, price_data)
        trade = trades[0]
        # Entry should be at dates[3] (next day after signal)
        assert trade.entry_date == dates[3].to_pydatetime()

    def test_position_size_applied(self):
        sim = TradeSimulator(position_size=10_000, stop_atr_multiple=0.0)
        price_data, dates = _simple_up_data(n=20)

        signals = [
            _make_signal("TEST", dates[2], SignalDirection.BUY),
            _make_signal("TEST", dates[10], SignalDirection.SELL),
        ]
        trades = sim.simulate(signals, price_data)
        trade = trades[0]
        assert trade.shares == int(10_000 / trade.entry_price)
        assert trade.pnl == pytest.approx(
            (trade.exit_price - trade.entry_price) * trade.shares, rel=1e-6
        )

    def test_unmatched_buy_closed_at_last_price(self):
        sim = TradeSimulator(stop_atr_multiple=0.0)
        price_data, dates = _simple_up_data(n=20)

        signals = [_make_signal("TEST", dates[5], SignalDirection.BUY)]
        trades = sim.simulate(signals, price_data)
        assert len(trades) == 1
        assert trades[0].exit_reason == "data_end"

    def test_stop_loss_triggered(self):
        sim = TradeSimulator(stop_atr_multiple=2.0, atr_lookback=5)
        # Create data where price drops sharply after entry
        dates = pd.bdate_range("2026-01-01", periods=20)
        opens = [100.0] * 5 + [100.0, 95.0, 90.0, 85.0, 80.0] + [75.0] * 10
        closes = [100.0] * 5 + [99.0, 94.0, 89.0, 84.0, 79.0] + [74.0] * 10
        highs = [101.0] * 5 + [101.0, 96.0, 91.0, 86.0, 81.0] + [76.0] * 10
        lows = [99.0] * 5 + [98.0, 93.0, 88.0, 83.0, 78.0] + [73.0] * 10
        price_data = _make_price_data("TEST", dates, opens, closes, highs, lows)

        signals = [
            _make_signal("TEST", dates[2], SignalDirection.BUY),
            _make_signal("TEST", dates[15], SignalDirection.SELL),
        ]
        trades = sim.simulate(signals, price_data)
        assert len(trades) == 1
        assert trades[0].exit_reason == "stop_loss"

    def test_multiple_tickers(self):
        sim = TradeSimulator(stop_atr_multiple=0.0)
        data1, dates1 = _simple_up_data("AAPL", n=20)
        data2, dates2 = _simple_up_data("MSFT", "2026-01-05", n=20)
        price_data = pd.concat([data1, data2])

        signals = [
            _make_signal("AAPL", dates1[2], SignalDirection.BUY),
            _make_signal("AAPL", dates1[10], SignalDirection.SELL),
            _make_signal("MSFT", dates2[2], SignalDirection.BUY),
            _make_signal("MSFT", dates2[10], SignalDirection.SELL),
        ]
        trades = sim.simulate(signals, price_data)
        assert len(trades) == 2
        tickers = {t.ticker for t in trades}
        assert tickers == {"AAPL", "MSFT"}

    def test_trades_sorted_by_entry_date(self):
        sim = TradeSimulator(stop_atr_multiple=0.0)
        data_a, dates_a = _simple_up_data("AAPL", n=20)
        data_b, dates_b = _simple_up_data("MSFT", "2026-01-03", n=20)
        price_data = pd.concat([data_a, data_b])

        signals = [
            _make_signal("MSFT", dates_b[5], SignalDirection.BUY),
            _make_signal("MSFT", dates_b[12], SignalDirection.SELL),
            _make_signal("AAPL", dates_a[2], SignalDirection.BUY),
            _make_signal("AAPL", dates_a[8], SignalDirection.SELL),
        ]
        trades = sim.simulate(signals, price_data)
        assert len(trades) == 2
        assert trades[0].entry_date <= trades[1].entry_date

    def test_no_stop_when_disabled(self):
        sim = TradeSimulator(stop_atr_multiple=0.0)
        dates = pd.bdate_range("2026-01-01", periods=20)
        opens = [100.0] * 5 + [100.0, 90.0, 80.0, 70.0, 60.0] + [50.0] * 10
        closes = [100.0] * 5 + [99.0, 89.0, 79.0, 69.0, 59.0] + [49.0] * 10
        price_data = _make_price_data("TEST", dates, opens, closes)

        signals = [
            _make_signal("TEST", dates[2], SignalDirection.BUY),
            _make_signal("TEST", dates[15], SignalDirection.SELL),
        ]
        trades = sim.simulate(signals, price_data)
        assert trades[0].exit_reason == "signal"

    def test_strategy_id_preserved(self):
        sim = TradeSimulator(stop_atr_multiple=0.0)
        price_data, dates = _simple_up_data(n=20)

        signals = [
            _make_signal("TEST", dates[2], SignalDirection.BUY, "my_strategy"),
            _make_signal("TEST", dates[10], SignalDirection.SELL, "my_strategy"),
        ]
        trades = sim.simulate(signals, price_data)
        assert trades[0].strategy_id == "my_strategy"
