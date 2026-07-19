"""Tests for thermaltrend.core.strategy — MACrossoverStrategy, DonchianBreakoutStrategy, RSIMeanReversionStrategy."""

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


def _market_ohlcv(ticker, date, open_, high, low, close):
    return MarketEvent(
        timestamp=date,
        ticker=ticker,
        open=open_,
        high=high,
        low=low,
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


class TestDonchianBreakoutStrategy:
    def test_no_signal_before_warmup(self):
        from thermaltrend.core.strategy import DonchianBreakoutStrategy

        s = DonchianBreakoutStrategy(entry_period=5, exit_period=3)
        dates = [datetime(2026, 1, i + 1) for i in range(4)]
        prices = [(d, 100.0 + i) for i, d in enumerate(dates)]
        signals = _run_strategy(prices, s)
        assert len(signals) == 0

    def test_buy_on_breakout(self):
        from thermaltrend.core.strategy import DonchianBreakoutStrategy

        s = DonchianBreakoutStrategy(entry_period=5, exit_period=3)
        dates = [datetime(2026, 1, i + 1) for i in range(7)]
        # First 5 bars: price stays in 100-104 range
        # Bar 5: breaks above 5-day high of 104
        prices = [
            (dates[0], 100.0), (dates[1], 101.0), (dates[2], 102.0),
            (dates[3], 103.0), (dates[4], 104.0),
            (dates[5], 110.0),  # breaks above 104
        ]
        signals = _run_strategy(prices, s)
        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.BUY

    def test_sell_on_breakdown(self):
        from thermaltrend.core.strategy import DonchianBreakoutStrategy

        s = DonchianBreakoutStrategy(entry_period=5, exit_period=3)
        dates = [datetime(2026, 1, i + 1) for i in range(8)]
        # First 5 bars: price rises 100-104
        # Bar 5: breaks above → BUY
        # Bars 6-7: price drops
        # Bar 7: breaks below 3-day exit low
        prices = [
            (dates[0], 100.0), (dates[1], 101.0), (dates[2], 102.0),
            (dates[3], 103.0), (dates[4], 104.0),
            (dates[5], 110.0),  # BUY
            (dates[6], 105.0), (dates[7], 95.0),  # breakdown below exit_low
        ]
        signals = _run_strategy(prices, s)
        assert len(signals) == 2
        assert signals[0].direction == SignalDirection.BUY
        assert signals[1].direction == SignalDirection.SELL

    def test_no_signal_within_channel(self):
        from thermaltrend.core.strategy import DonchianBreakoutStrategy

        s = DonchianBreakoutStrategy(entry_period=5, exit_period=3)
        dates = [datetime(2026, 1, i + 1) for i in range(8)]
        # Price oscillates but stays within channel
        prices = [
            (dates[0], 100.0), (dates[1], 101.0), (dates[2], 102.0),
            (dates[3], 103.0), (dates[4], 104.0),
            (dates[5], 103.0), (dates[6], 102.0), (dates[7], 103.0),
        ]
        signals = _run_strategy(prices, s)
        assert len(signals) == 0

    def test_strength_bounded(self):
        from thermaltrend.core.strategy import DonchianBreakoutStrategy

        s = DonchianBreakoutStrategy(entry_period=5, exit_period=3)
        dates = [datetime(2026, 1, i + 1) for i in range(6)]
        prices = [
            (dates[0], 100.0), (dates[1], 101.0), (dates[2], 102.0),
            (dates[3], 103.0), (dates[4], 104.0),
            (dates[5], 110.0),
        ]
        signals = _run_strategy(prices, s)
        assert len(signals) == 1
        assert 0.0 <= signals[0].strength <= 1.0

    def test_strategy_id(self):
        from thermaltrend.core.strategy import DonchianBreakoutStrategy

        s = DonchianBreakoutStrategy(entry_period=5, exit_period=3, strategy_id="my_don")
        dates = [datetime(2026, 1, i + 1) for i in range(6)]
        prices = [
            (dates[0], 100.0), (dates[1], 101.0), (dates[2], 102.0),
            (dates[3], 103.0), (dates[4], 104.0),
            (dates[5], 110.0),
        ]
        signals = _run_strategy(prices, s)
        assert signals[0].strategy_id == "my_don"

    def test_metadata_populated(self):
        from thermaltrend.core.strategy import DonchianBreakoutStrategy

        s = DonchianBreakoutStrategy(entry_period=5, exit_period=3)
        dates = [datetime(2026, 1, i + 1) for i in range(6)]
        prices = [
            (dates[0], 100.0), (dates[1], 101.0), (dates[2], 102.0),
            (dates[3], 103.0), (dates[4], 104.0),
            (dates[5], 110.0),
        ]
        signals = _run_strategy(prices, s)
        assert "channel_high" in signals[0].metadata
        assert "channel_low" in signals[0].metadata
        assert signals[0].metadata["entry_period"] == 5
        assert signals[0].metadata["exit_period"] == 3

    def test_independent_tickers(self):
        from thermaltrend.core.strategy import DonchianBreakoutStrategy

        s = DonchianBreakoutStrategy(entry_period=5, exit_period=3)
        dates = [datetime(2026, 1, i + 1) for i in range(6)]

        # AAPL: breakout above 104
        for i, d in enumerate(dates):
            close = [100, 101, 102, 103, 104, 110][i]
            s.on_market(_market("AAPL", d, close))

        # MSFT: no breakout (price stays flat)
        for i, d in enumerate(dates):
            close = [100, 100, 100, 100, 100, 100][i]
            s.on_market(_market("MSFT", d, close))

        assert len(s._highs["AAPL"]) == 6
        assert len(s._highs["MSFT"]) == 6
        assert s._in_position["AAPL"] is True
        assert s._in_position["MSFT"] is False


class TestRSIMeanReversionStrategy:
    def test_no_signal_before_warmup(self):
        from thermaltrend.core.strategy import RSIMeanReversionStrategy

        s = RSIMeanReversionStrategy(period=3)
        dates = [datetime(2026, 1, i + 1) for i in range(3)]
        prices = [(d, 100.0) for d in dates]
        signals = _run_strategy(prices, s)
        assert len(signals) == 0

    def test_buy_on_oversold_cross(self):
        from thermaltrend.core.strategy import RSIMeanReversionStrategy

        s = RSIMeanReversionStrategy(period=3, oversold=30.0, overbought=70.0)
        dates = [datetime(2026, 1, i + 1) for i in range(10)]
        # Create a price sequence that generates RSI < 30, then recovers
        prices = [
            (dates[0], 100.0), (dates[1], 95.0), (dates[2], 90.0),
            (dates[3], 85.0), (dates[4], 80.0),
            # RSI should be low here, then recover
            (dates[5], 85.0), (dates[6], 90.0),
        ]
        signals = _run_strategy(prices, s)
        # Should get at least one BUY signal when RSI crosses back above 30
        buy_signals = [s for s in signals if s.direction == SignalDirection.BUY]
        assert len(buy_signals) >= 1

    def test_sell_on_overbought_cross(self):
        from thermaltrend.core.strategy import RSIMeanReversionStrategy

        s = RSIMeanReversionStrategy(period=3, oversold=30.0, overbought=70.0)
        dates = [datetime(2026, 1, i + 1) for i in range(10)]
        # Create a price sequence that generates RSI > 70, then drops
        prices = [
            (dates[0], 100.0), (dates[1], 105.0), (dates[2], 110.0),
            (dates[3], 115.0), (dates[4], 120.0),
            # RSI should be high here, then drop
            (dates[5], 115.0), (dates[6], 110.0),
        ]
        signals = _run_strategy(prices, s)
        sell_signals = [s for s in signals if s.direction == SignalDirection.SELL]
        assert len(sell_signals) >= 1

    def test_no_signal_in_neutral_zone(self):
        from thermaltrend.core.strategy import RSIMeanReversionStrategy

        s = RSIMeanReversionStrategy(period=3, oversold=30.0, overbought=70.0)
        dates = [datetime(2026, 1, i + 1) for i in range(10)]
        # RSI stays in neutral zone (30-70)
        prices = [
            (dates[0], 100.0), (dates[1], 100.5), (dates[2], 100.0),
            (dates[3], 100.5), (dates[4], 100.0),
            (dates[5], 100.5), (dates[6], 100.0),
        ]
        signals = _run_strategy(prices, s)
        assert len(signals) == 0

    def test_strength_bounded(self):
        from thermaltrend.core.strategy import RSIMeanReversionStrategy

        s = RSIMeanReversionStrategy(period=3, oversold=30.0, overbought=70.0)
        dates = [datetime(2026, 1, i + 1) for i in range(10)]
        prices = [
            (dates[0], 100.0), (dates[1], 95.0), (dates[2], 90.0),
            (dates[3], 85.0), (dates[4], 80.0),
            (dates[5], 85.0), (dates[6], 90.0),
        ]
        signals = _run_strategy(prices, s)
        for sig in signals:
            assert 0.0 <= sig.strength <= 1.0

    def test_strategy_id(self):
        from thermaltrend.core.strategy import RSIMeanReversionStrategy

        s = RSIMeanReversionStrategy(period=3, strategy_id="my_rsi")
        dates = [datetime(2026, 1, i + 1) for i in range(10)]
        prices = [
            (dates[0], 100.0), (dates[1], 95.0), (dates[2], 90.0),
            (dates[3], 85.0), (dates[4], 80.0),
            (dates[5], 85.0), (dates[6], 90.0),
        ]
        signals = _run_strategy(prices, s)
        for sig in signals:
            assert sig.strategy_id == "my_rsi"

    def test_metadata_populated(self):
        from thermaltrend.core.strategy import RSIMeanReversionStrategy

        s = RSIMeanReversionStrategy(period=3, oversold=30.0, overbought=70.0)
        dates = [datetime(2026, 1, i + 1) for i in range(10)]
        prices = [
            (dates[0], 100.0), (dates[1], 95.0), (dates[2], 90.0),
            (dates[3], 85.0), (dates[4], 80.0),
            (dates[5], 85.0), (dates[6], 90.0),
        ]
        signals = _run_strategy(prices, s)
        assert len(signals) > 0
        for sig in signals:
            assert "rsi" in sig.metadata
            assert "oversold" in sig.metadata
            assert "overbought" in sig.metadata
            assert "period" in sig.metadata

    def test_independent_tickers(self):
        from thermaltrend.core.strategy import RSIMeanReversionStrategy

        s = RSIMeanReversionStrategy(period=3, oversold=30.0, overbought=70.0)
        dates = [datetime(2026, 1, i + 1) for i in range(10)]

        # AAPL: drops then recovers (should trigger BUY)
        for i, d in enumerate(dates):
            close = [100, 95, 90, 85, 80, 85, 90, 95, 100, 105][i]
            s.on_market(_market("AAPL", d, close))

        # MSFT: flat (no signals)
        for i, d in enumerate(dates):
            close = [100, 100, 100, 100, 100, 100, 100, 100, 100, 100][i]
            s.on_market(_market("MSFT", d, close))

        assert len(s._prices["AAPL"]) == 10
        assert len(s._prices["MSFT"]) == 10

    def test_rsi_all_gains_no_losses(self):
        from thermaltrend.core.strategy import RSIMeanReversionStrategy

        s = RSIMeanReversionStrategy(period=3, oversold=30.0, overbought=70.0)
        dates = [datetime(2026, 1, i + 1) for i in range(10)]
        # Strictly rising prices — RSI should be 100, no SELL triggered
        # because RSI never crosses back below overbought
        prices = [
            (dates[0], 100.0), (dates[1], 101.0), (dates[2], 102.0),
            (dates[3], 103.0), (dates[4], 104.0),
            (dates[5], 105.0), (dates[6], 106.0),
        ]
        signals = _run_strategy(prices, s)
        # RSI=100 means avg_loss=0, strategy should not crash
        assert all(0.0 <= sig.strength <= 1.0 for sig in signals)


class TestDonchianBreakoutReEntry:
    def test_re_entry_after_exit(self):
        from thermaltrend.core.strategy import DonchianBreakoutStrategy

        s = DonchianBreakoutStrategy(entry_period=3, exit_period=2)
        dates = [datetime(2026, 1, i + 1) for i in range(10)]
        # Phase 1: breakout → BUY
        # Phase 2: breakdown → SELL
        # Phase 3: new breakout → BUY again
        prices = [
            (dates[0], 100.0), (dates[1], 101.0), (dates[2], 102.0),
            (dates[3], 108.0),  # BUY (breaks above 102)
            (dates[4], 107.0), (dates[5], 95.0),  # SELL (breaks below exit_low)
            (dates[6], 96.0), (dates[7], 97.0),
            (dates[8], 105.0),  # BUY again (breaks above new channel high)
        ]
        signals = _run_strategy(prices, s)
        assert len(signals) == 3
        assert signals[0].direction == SignalDirection.BUY
        assert signals[1].direction == SignalDirection.SELL
        assert signals[2].direction == SignalDirection.BUY
