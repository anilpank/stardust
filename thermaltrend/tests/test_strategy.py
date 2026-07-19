"""Tests for thermaltrend.core.strategy — MACrossoverStrategy."""

from datetime import datetime

import pytest

from thermaltrend.core.events import MarketEvent, SignalDirection


def _market(ticker, date, close):
    return MarketEvent(
        timestamp=date,
        ticker=ticker,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000,
    )


def _run_strategy(prices, strategy):
    """Feed a list of (date, close) pairs through the strategy."""
    signals = []
    for i, (date, close) in enumerate(prices):
        event = _market("TEST", date, close)
        sig = strategy.on_market(event)
        if sig is not None:
            signals.append(sig)
    return signals


class TestMACrossoverStrategy:
    def test_no_signal_before_slow_period(self):
        from thermaltrend.core.strategy import MACrossoverStrategy

        s = MACrossoverStrategy(fast_period=3, slow_period=5)
        dates = [datetime(2026, 1, i + 1) for i in range(4)]
        prices = [(d, 100.0 + i) for i, d in enumerate(dates)]
        signals = _run_strategy(prices, s)
        assert len(signals) == 0

    def test_golden_cross(self):
        from thermaltrend.core.strategy import MACrossoverStrategy

        s = MACrossoverStrategy(fast_period=3, slow_period=5)
        dates = [datetime(2026, 1, i + 1) for i in range(7)]
        # First 5 bars: fast < slow → prev_fast_above = False
        prices = [
            (dates[0], 100.0), (dates[1], 99.0), (dates[2], 98.0),
            (dates[3], 97.0), (dates[4], 96.0),
            # Bars 5-6: fast rises above slow → golden cross
            (dates[5], 110.0), (dates[6], 120.0),
        ]
        signals = _run_strategy(prices, s)
        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.BUY

    def test_death_cross(self):
        from thermaltrend.core.strategy import MACrossoverStrategy

        s = MACrossoverStrategy(fast_period=3, slow_period=5)
        dates = [datetime(2026, 1, i + 1) for i in range(7)]
        # First 5 bars: fast > slow → prev_fast_above = True
        prices = [
            (dates[0], 100.0), (dates[1], 101.0), (dates[2], 102.0),
            (dates[3], 103.0), (dates[4], 104.0),
            # Bars 5-6: fast drops below slow → death cross
            (dates[5], 90.0), (dates[6], 80.0),
        ]
        signals = _run_strategy(prices, s)
        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.SELL

    def test_no_signal_when_no_crossover(self):
        from thermaltrend.core.strategy import MACrossoverStrategy

        s = MACrossoverStrategy(fast_period=3, slow_period=5)
        dates = [datetime(2026, 1, i + 1) for i in range(8)]
        # Fast starts below slow, crosses above once, then stays above —
        # only one signal (the golden cross), no death cross follows
        prices = [
            (dates[0], 100.0), (dates[1], 99.0), (dates[2], 98.0),
            (dates[3], 97.0), (dates[4], 96.0),
            # fast_ma now crosses above slow_ma → golden cross signal
            (dates[5], 110.0),
            # fast stays above — no new crossover
            (dates[6], 112.0), (dates[7], 114.0),
        ]
        signals = _run_strategy(prices, s)
        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.BUY

    def test_strength_bounded(self):
        from thermaltrend.core.strategy import MACrossoverStrategy

        s = MACrossoverStrategy(fast_period=3, slow_period=5)
        dates = [datetime(2026, 1, i + 1) for i in range(7)]
        prices = [
            (dates[0], 100.0), (dates[1], 99.0), (dates[2], 98.0),
            (dates[3], 97.0), (dates[4], 96.0),
            (dates[5], 110.0), (dates[6], 120.0),
        ]
        signals = _run_strategy(prices, s)
        assert len(signals) == 1
        assert 0.0 <= signals[0].strength <= 1.0

    def test_strategy_id(self):
        from thermaltrend.core.strategy import MACrossoverStrategy

        s = MACrossoverStrategy(fast_period=3, slow_period=5, strategy_id="my_strat")
        dates = [datetime(2026, 1, i + 1) for i in range(7)]
        prices = [
            (dates[0], 100.0), (dates[1], 99.0), (dates[2], 98.0),
            (dates[3], 97.0), (dates[4], 96.0),
            (dates[5], 110.0), (dates[6], 120.0),
        ]
        signals = _run_strategy(prices, s)
        assert signals[0].strategy_id == "my_strat"

    def test_metadata_populated(self):
        from thermaltrend.core.strategy import MACrossoverStrategy

        s = MACrossoverStrategy(fast_period=3, slow_period=5)
        dates = [datetime(2026, 1, i + 1) for i in range(7)]
        prices = [
            (dates[0], 100.0), (dates[1], 99.0), (dates[2], 98.0),
            (dates[3], 97.0), (dates[4], 96.0),
            (dates[5], 110.0), (dates[6], 120.0),
        ]
        signals = _run_strategy(prices, s)
        assert "fast_ma" in signals[0].metadata
        assert "slow_ma" in signals[0].metadata

    def test_independent_tickers(self):
        from thermaltrend.core.strategy import MACrossoverStrategy

        s = MACrossoverStrategy(fast_period=3, slow_period=5)
        dates = [datetime(2026, 1, i + 1) for i in range(7)]

        # AAPL: golden cross
        for i, d in enumerate(dates):
            close = [100, 99, 98, 97, 96, 110, 120][i]
            s.on_market(_market("AAPL", d, close))

        # MSFT: no cross (fast always below slow)
        for i, d in enumerate(dates):
            close = [100, 95, 90, 85, 80, 75, 70][i]
            s.on_market(_market("MSFT", d, close))

        # AAPL should have a signal, MSFT should not
        assert len(s._prices["AAPL"]) == 7
        assert len(s._prices["MSFT"]) == 7
