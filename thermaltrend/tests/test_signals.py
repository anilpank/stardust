"""Tests for thermaltrend/signals.py — CLI signal output tool."""

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from thermaltrend.signals import format_signals_table
from thermaltrend.core.events import SignalDirection, SignalEvent


def _signal(ticker, direction, strength, day=1):
    return SignalEvent(
        timestamp=datetime(2026, 1, day),
        ticker=ticker,
        direction=direction,
        strength=strength,
        strategy_id="test",
        metadata={"fast_ma": 150.1234, "slow_ma": 145.5678},
    )


class TestFormatSignalsTable:
    def test_empty_signals(self):
        result = format_signals_table([], "test")
        assert "No signals" in result

    def test_single_signal(self):
        signals = [_signal("AAPL", SignalDirection.BUY, 0.85)]
        result = format_signals_table(signals, "test")
        assert "AAPL" in result
        assert "BUY" in result
        assert "0.8500" in result

    def test_sorted_by_strength_descending(self):
        signals = [
            _signal("AAPL", SignalDirection.BUY, 0.3),
            _signal("MSFT", SignalDirection.SELL, 0.9),
            _signal("GOOG", SignalDirection.BUY, 0.6),
        ]
        result = format_signals_table(signals, "test")
        lines = result.strip().split("\n")
        data_lines = [l for l in lines[3:] if l.strip()]  # skip header
        assert "MSFT" in data_lines[0]
        assert "GOOG" in data_lines[1]
        assert "AAPL" in data_lines[2]

    def test_strategy_name_in_header(self):
        signals = [_signal("AAPL", SignalDirection.BUY, 0.8)]
        result = format_signals_table(signals, "my_strategy")
        assert "my_strategy" in result

    def test_metadata_shown(self):
        signals = [_signal("AAPL", SignalDirection.BUY, 0.8)]
        result = format_signals_table(signals, "test")
        assert "150.1234" in result
        assert "145.5678" in result


class TestSignalsIntegration:
    def _make_parquet(self, tmp_path, ticker, dates_closes):
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

    def test_end_to_end(self, tmp_path):
        """Full pipeline: parquet → DataFeed → DataEngine → signals."""
        from thermaltrend.core.engine import DataEngine
        from thermaltrend.core.strategy import MACrossoverStrategy
        from thermaltrend.feed import DataFeed

        dates = [datetime(2026, 1, i + 1) for i in range(10)]
        prices = [100, 99, 98, 97, 96, 110, 120, 130, 140, 150]
        closes = [(d, float(p)) for d, p in zip(dates, prices)]
        self._make_parquet(tmp_path, "TEST", closes)

        feed = DataFeed(tmp_path)
        strategy = MACrossoverStrategy(fast_period=3, slow_period=5)
        engine = DataEngine(feed, strategy)
        signals = engine.run()

        assert len(signals) == 1
        table = format_signals_table(signals, "ma_crossover")
        assert "TEST" in table
        assert "BUY" in table
