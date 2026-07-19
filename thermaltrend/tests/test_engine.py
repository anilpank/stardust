"""Tests for thermaltrend.core.engine — DataEngine integration."""

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from thermaltrend.core.engine import DataEngine
from thermaltrend.core.events import MarketEvent, SignalDirection, SignalEvent
from thermaltrend.core.strategy import MACrossoverStrategy
from thermaltrend.feed import DataFeed


def _make_parquet(tmp_path, ticker, dates_closes):
    """Create a Parquet file with Date index and ticker column."""
    df = pd.DataFrame(
        {"Open": [c for _, c in dates_closes],
         "High": [c for _, c in dates_closes],
         "Low": [c for _, c in dates_closes],
         "Close": [c for _, c in dates_closes],
         "Volume": [1000] * len(dates_closes),
         "ticker": ticker},
        index=pd.DatetimeIndex([d for d, _ in dates_closes], name="Date"),
    )
    df.to_parquet(tmp_path / f"{ticker}.parquet")


class _StubStrategy:
    """Collects all MarketEvents without producing signals."""

    def __init__(self):
        self.events = []

    def on_market(self, event):
        self.events.append(event)
        return None


class _SingleSignalStrategy:
    """Produces one signal on the first bar."""

    def __init__(self):
        self.call_count = 0

    def on_market(self, event):
        self.call_count += 1
        if self.call_count == 1:
            return SignalEvent(
                timestamp=event.timestamp,
                ticker=event.ticker,
                direction=SignalDirection.BUY,
                strength=0.95,
                strategy_id="stub",
            )
        return None


class TestDataEngine:
    def test_run_produces_signals(self, tmp_path):
        dates = [datetime(2026, 1, i + 1) for i in range(10)]
        closes = [(d, 100.0 + i * 0.5) for i, d in enumerate(dates)]
        _make_parquet(tmp_path, "AAPL", closes)

        feed = DataFeed(tmp_path)
        strategy = _SingleSignalStrategy()
        engine = DataEngine(feed, strategy)

        signals = engine.run()
        assert len(signals) == 1
        assert signals[0].ticker == "AAPL"

    def test_run_no_signals(self, tmp_path):
        dates = [datetime(2026, 1, i + 1) for i in range(10)]
        closes = [(d, 100.0) for d in dates]
        _make_parquet(tmp_path, "AAPL", closes)

        feed = DataFeed(tmp_path)
        strategy = _StubStrategy()
        engine = DataEngine(feed, strategy)

        signals = engine.run()
        assert len(signals) == 0
        assert len(strategy.events) == 10

    def test_multiple_tickers(self, tmp_path):
        dates = [datetime(2026, 1, i + 1) for i in range(5)]
        _make_parquet(tmp_path, "AAPL", [(d, 100.0) for d in dates])
        _make_parquet(tmp_path, "MSFT", [(d, 200.0) for d in dates])

        feed = DataFeed(tmp_path)
        strategy = _StubStrategy()
        engine = DataEngine(feed, strategy)

        engine.run()
        tickers_seen = {e.ticker for e in strategy.events}
        assert tickers_seen == {"AAPL", "MSFT"}

    def test_events_sorted_by_timestamp_and_ticker(self, tmp_path):
        dates = [datetime(2026, 1, i + 1) for i in range(5)]
        _make_parquet(tmp_path, "MSFT", [(d, 200.0) for d in dates])
        _make_parquet(tmp_path, "AAPL", [(d, 100.0) for d in dates])

        feed = DataFeed(tmp_path)
        strategy = _SingleSignalStrategy()
        engine = DataEngine(feed, strategy)

        signals = engine.run()
        assert len(signals) == 1

    def test_chronological_order(self, tmp_path):
        """Verify events are processed in date order, not ticker order."""
        d1 = datetime(2026, 1, 1)
        d2 = datetime(2026, 1, 2)
        _make_parquet(tmp_path, "ZZZZ", [(d1, 100.0), (d2, 100.0)])
        _make_parquet(tmp_path, "AAAA", [(d1, 200.0), (d2, 200.0)])

        feed = DataFeed(tmp_path)
        strategy = _StubStrategy()
        engine = DataEngine(feed, strategy)

        engine.run()
        dates_seen = [e.timestamp for e in strategy.events]
        # All d1 events before all d2 events
        d1_indices = [i for i, d in enumerate(dates_seen) if d == d1]
        d2_indices = [i for i, d in enumerate(dates_seen) if d == d2]
        assert max(d1_indices) < min(d2_indices)

    def test_ma_crossover_integration(self, tmp_path):
        """Full integration: DataFeed → DataEngine → MACrossoverStrategy."""
        dates = [datetime(2026, 1, i + 1) for i in range(10)]
        prices = [
            100, 99, 98, 97, 96,  # fast < slow
            110, 120, 130, 140, 150,  # fast > slow → golden cross
        ]
        closes = [(d, float(p)) for d, p in zip(dates, prices)]
        _make_parquet(tmp_path, "TEST", closes)

        feed = DataFeed(tmp_path)
        strategy = MACrossoverStrategy(fast_period=3, slow_period=5)
        engine = DataEngine(feed, strategy)

        signals = engine.run()
        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.BUY
        assert signals[0].ticker == "TEST"
