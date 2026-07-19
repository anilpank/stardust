"""Tests for thermaltrend.core.events — Event types and EventQueue."""

from datetime import datetime

import pytest

from thermaltrend.core.events import (
    EventQueue,
    EventType,
    MarketEvent,
    SignalDirection,
    SignalEvent,
)

BAR_TS = datetime(2026, 1, 15, 16, 0)
SIGNAL_TS = datetime(2026, 1, 15, 16, 5)


def _market_event(ticker="AAPL", ts=None):
    return MarketEvent(
        timestamp=ts or BAR_TS,
        ticker=ticker,
        open=150.0,
        high=155.0,
        low=148.0,
        close=153.0,
        volume=1_000_000,
    )


def _signal_event(ticker="AAPL", direction=SignalDirection.BUY, strength=0.8, ts=None):
    return SignalEvent(
        timestamp=ts or SIGNAL_TS,
        ticker=ticker,
        direction=direction,
        strength=strength,
        strategy_id="test",
    )


# ── MarketEvent ──────────────────────────────────────────────


class TestMarketEvent:
    def test_create(self):
        e = _market_event()
        assert e.ticker == "AAPL"
        assert e.close == 153.0
        assert e.timestamp == BAR_TS

    def test_immutable(self):
        e = _market_event()
        with pytest.raises(AttributeError):
            e.ticker = "MSFT"

    def test_event_type(self):
        e = _market_event()
        assert EventType.MARKET.value == "market"


# ── SignalEvent ──────────────────────────────────────────────


class TestSignalEvent:
    def test_create(self):
        s = _signal_event()
        assert s.ticker == "AAPL"
        assert s.direction == SignalDirection.BUY
        assert s.strength == 0.8
        assert s.strategy_id == "test"

    def test_immutable(self):
        s = _signal_event()
        with pytest.raises(AttributeError):
            s.strength = 0.9

    def test_default_metadata(self):
        s = _signal_event()
        assert s.metadata == {}

    def test_custom_metadata(self):
        s = SignalEvent(
            timestamp=SIGNAL_TS,
            ticker="AAPL",
            direction=SignalDirection.BUY,
            strength=0.8,
            strategy_id="test",
            metadata={"fast_ma": 150.0, "slow_ma": 145.0},
        )
        assert s.metadata["fast_ma"] == 150.0

    def test_unique_ids(self):
        s1 = _signal_event()
        s2 = _signal_event()
        assert s1.id != s2.id

    def test_signal_direction_values(self):
        assert SignalDirection.BUY.value == "BUY"
        assert SignalDirection.SELL.value == "SELL"
        assert SignalDirection.HOLD.value == "HOLD"


# ── EventQueue ───────────────────────────────────────────────


class TestEventQueue:
    def test_create_empty(self):
        q = EventQueue()
        assert q.is_empty()
        assert len(q) == 0

    def test_put_and_get(self):
        q = EventQueue()
        e = _market_event()
        q.put(e)
        assert q.get() is e
        assert q.is_empty()

    def test_fifo_order(self):
        q = EventQueue()
        e1 = _market_event(ticker="AAPL")
        e2 = _market_event(ticker="MSFT")
        e3 = _market_event(ticker="GOOG")
        q.put(e1)
        q.put(e2)
        q.put(e3)
        assert q.get() is e1
        assert q.get() is e2
        assert q.get() is e3

    def test_get_from_empty(self):
        q = EventQueue()
        assert q.get() is None

    def test_len(self):
        q = EventQueue()
        assert len(q) == 0
        q.put(_market_event())
        assert len(q) == 1
        q.put(_signal_event())
        assert len(q) == 2
        q.get()
        assert len(q) == 1

    def test_put_get_interleaved(self):
        q = EventQueue()
        e1 = _market_event(ticker="AAPL")
        e2 = _market_event(ticker="MSFT")
        q.put(e1)
        assert q.get() is e1
        q.put(e2)
        assert q.get() is e2
        assert q.is_empty()

    def test_repr(self):
        q = EventQueue()
        assert "len=0" in repr(q)
        q.put(_market_event())
        assert "len=1" in repr(q)

    def test_mixed_event_types(self):
        q = EventQueue()
        m = _market_event()
        s = _signal_event()
        q.put(m)
        q.put(s)
        assert q.get() is m
        assert q.get() is s
