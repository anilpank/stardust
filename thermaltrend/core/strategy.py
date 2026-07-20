"""
Strategy base class and implementations.

Strategies consume MarketEvents and produce SignalEvents.
Each strategy is stateful — it maintains internal state (e.g., price history,
indicator values) across calls to on_market().
"""

from abc import ABC, abstractmethod

from thermaltrend.core.events import MarketEvent, SignalDirection, SignalEvent


class Strategy(ABC):
    """Abstract base class for all trading strategies.

    Subclass this and implement on_market() to create a new strategy.
    """

    @abstractmethod
    def on_market(self, event: MarketEvent) -> SignalEvent | None:
        """Process a market event and optionally emit a signal.

        Args:
            event: The latest market bar.

        Returns:
            A SignalEvent if the strategy has a signal, None otherwise.
        """
        ...


class MACrossoverStrategy(Strategy):
    """Moving Average Crossover strategy.

    Generates BUY when fast MA crosses above slow MA (golden cross).
    Generates SELL when fast MA crosses below slow MA (death cross).

    Signal strength is based on the separation between MAs relative to price.
    Requires ``slow_period`` bars of history before producing any signals.
    """

    def __init__(
        self,
        fast_period: int = 50,
        slow_period: int = 200,
        strategy_id: str = "ma_crossover",
    ) -> None:
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.strategy_id = strategy_id
        self._prices: dict[str, list[float]] = {}
        self._prev_fast_above: dict[str, bool | None] = {}

    def on_market(self, event: MarketEvent) -> SignalEvent | None:
        if event.ticker not in self._prices:
            self._prices[event.ticker] = []
            self._prev_fast_above[event.ticker] = None

        self._prices[event.ticker].append(event.close)
        prices = self._prices[event.ticker]

        if len(prices) < self.slow_period:
            return None

        fast_ma = sum(prices[-self.fast_period :]) / self.fast_period
        slow_ma = sum(prices[-self.slow_period :]) / self.slow_period

        fast_above = fast_ma > slow_ma
        prev = self._prev_fast_above[event.ticker]
        self._prev_fast_above[event.ticker] = fast_above

        if prev is not None and fast_above != prev:
            strength = min(abs(fast_ma - slow_ma) / slow_ma * 10, 1.0)
            direction = SignalDirection.BUY if fast_above else SignalDirection.SELL
            return SignalEvent(
                timestamp=event.timestamp,
                ticker=event.ticker,
                direction=direction,
                strength=round(strength, 4),
                strategy_id=self.strategy_id,
                metadata={
                    "fast_ma": round(fast_ma, 4),
                    "slow_ma": round(slow_ma, 4),
                    "fast_period": self.fast_period,
                    "slow_period": self.slow_period,
                },
            )

        return None


class DonchianBreakoutStrategy(Strategy):
    """Donchian Channel Breakout strategy.

    Generates BUY when close breaks above the N-day high (channel breakout).
    Generates SELL when close breaks below the M-day low (channel exit).

    Uses asymmetric entry/exit periods — entry looks back further than exit
    to capture trend continuation while exiting quickly on breakdowns.
    """

    def __init__(
        self,
        entry_period: int = 20,
        exit_period: int = 10,
        strategy_id: str = "donchian",
    ) -> None:
        self.entry_period = entry_period
        self.exit_period = exit_period
        self.strategy_id = strategy_id
        self._highs: dict[str, list[float]] = {}
        self._lows: dict[str, list[float]] = {}
        self._in_position: dict[str, bool] = {}

    def on_market(self, event: MarketEvent) -> SignalEvent | None:
        ticker = event.ticker
        if ticker not in self._highs:
            self._highs[ticker] = []
            self._lows[ticker] = []
            self._in_position[ticker] = False

        self._highs[ticker].append(event.high)
        self._lows[ticker].append(event.low)
        highs = self._highs[ticker]
        lows = self._lows[ticker]

        if len(highs) < self.entry_period + 1:
            return None

        channel_high = max(highs[-(self.entry_period + 1) : -1])
        channel_low = min(lows[-(self.exit_period + 1) : -1])

        if not self._in_position[ticker]:
            if event.close > channel_high:
                self._in_position[ticker] = True
                strength = min((event.close - channel_high) / channel_high * 10, 1.0)
                return SignalEvent(
                    timestamp=event.timestamp,
                    ticker=ticker,
                    direction=SignalDirection.BUY,
                    strength=round(max(strength, 0.01), 4),
                    strategy_id=self.strategy_id,
                    metadata={
                        "channel_high": round(channel_high, 4),
                        "channel_low": round(channel_low, 4),
                        "entry_period": self.entry_period,
                        "exit_period": self.exit_period,
                    },
                )
        else:
            if event.close < channel_low:
                self._in_position[ticker] = False
                strength = min((channel_low - event.close) / channel_low * 10, 1.0)
                return SignalEvent(
                    timestamp=event.timestamp,
                    ticker=ticker,
                    direction=SignalDirection.SELL,
                    strength=round(max(strength, 0.01), 4),
                    strategy_id=self.strategy_id,
                    metadata={
                        "channel_high": round(channel_high, 4),
                        "channel_low": round(channel_low, 4),
                        "entry_period": self.entry_period,
                        "exit_period": self.exit_period,
                    },
                )

        return None


class RSIMeanReversionStrategy(Strategy):
    """RSI Mean Reversion strategy.

    Generates BUY when RSI crosses back above the oversold threshold (default 30).
    Generates SELL when RSI crosses back below the overbought threshold (default 70).

    Uses Wilder's smoothing method for RSI calculation. Requires ``period + 1``
    bars of history before producing any signals.
    """

    def __init__(
        self,
        period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
        strategy_id: str = "rsi_mean_reversion",
    ) -> None:
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.strategy_id = strategy_id
        self._prices: dict[str, list[float]] = {}
        self._prev_rsi: dict[str, float | None] = {}
        self._avg_gain: dict[str, float] = {}
        self._avg_loss: dict[str, float] = {}
        self._initialized: dict[str, bool] = {}

    def _compute_rsi(self, ticker: str) -> float | None:
        prices = self._prices[ticker]
        if len(prices) < self.period + 1:
            return None

        if not self._initialized[ticker]:
            gains = []
            losses = []
            for i in range(1, self.period + 1):
                change = prices[i] - prices[i - 1]
                gains.append(max(change, 0.0))
                losses.append(max(-change, 0.0))
            avg_gain = sum(gains) / self.period
            avg_loss = sum(losses) / self.period
            self._avg_gain[ticker] = avg_gain
            self._avg_loss[ticker] = avg_loss
            self._initialized[ticker] = True
        else:
            change = prices[-1] - prices[-2]
            current_gain = max(change, 0.0)
            current_loss = max(-change, 0.0)
            self._avg_gain[ticker] = (
                self._avg_gain[ticker] * (self.period - 1) + current_gain
            ) / self.period
            self._avg_loss[ticker] = (
                self._avg_loss[ticker] * (self.period - 1) + current_loss
            ) / self.period

        if self._avg_loss[ticker] == 0:
            return 100.0
        rs = self._avg_gain[ticker] / self._avg_loss[ticker]
        return 100.0 - 100.0 / (1.0 + rs)

    def on_market(self, event: MarketEvent) -> SignalEvent | None:
        ticker = event.ticker
        if ticker not in self._prices:
            self._prices[ticker] = []
            self._prev_rsi[ticker] = None
            self._initialized[ticker] = False

        self._prices[ticker].append(event.close)
        rsi = self._compute_rsi(ticker)

        if rsi is None:
            return None

        prev_rsi = self._prev_rsi[ticker]
        self._prev_rsi[ticker] = rsi

        if prev_rsi is None:
            return None

        if prev_rsi < self.oversold and rsi >= self.oversold:
            strength = min((self.oversold - prev_rsi) / self.oversold, 1.0)
            return SignalEvent(
                timestamp=event.timestamp,
                ticker=ticker,
                direction=SignalDirection.BUY,
                strength=round(max(strength, 0.01), 4),
                strategy_id=self.strategy_id,
                metadata={
                    "rsi": round(rsi, 4),
                    "prev_rsi": round(prev_rsi, 4),
                    "oversold": self.oversold,
                    "overbought": self.overbought,
                    "period": self.period,
                },
            )

        if prev_rsi > self.overbought and rsi <= self.overbought:
            strength = min(
                (prev_rsi - self.overbought) / (100.0 - self.overbought), 1.0
            )
            return SignalEvent(
                timestamp=event.timestamp,
                ticker=ticker,
                direction=SignalDirection.SELL,
                strength=round(max(strength, 0.01), 4),
                strategy_id=self.strategy_id,
                metadata={
                    "rsi": round(rsi, 4),
                    "prev_rsi": round(prev_rsi, 4),
                    "oversold": self.oversold,
                    "overbought": self.overbought,
                    "period": self.period,
                },
            )

        return None


class ATRTrailingStopStrategy(Strategy):
    """ATR Trailing Stop (Chandelier Exit) strategy.

    Generates BUY when close breaks above the N-day high (breakout entry).
    Generates SELL when close drops below the trailing stop level, which is
    computed as the highest high since entry minus ``atr_multiple`` times ATR.

    The trailing stop only ratchets upward — it never moves down. This ensures
    the exit level tightens as the trend extends, protecting accumulated gains.

    Requires ``entry_period + 1`` bars before producing any signals (same as
    DonchianBreakoutStrategy). ATR requires ``atr_period + 1`` bars, but since
    ``entry_period`` (default 20) > ``atr_period`` (default 14), ATR is always
    ready before the first possible entry signal.
    """

    def __init__(
        self,
        entry_period: int = 20,
        atr_period: int = 14,
        atr_multiple: float = 3.0,
        strategy_id: str = "atr_trailing_stop",
    ) -> None:
        self.entry_period = entry_period
        self.atr_period = atr_period
        self.atr_multiple = atr_multiple
        self.strategy_id = strategy_id
        self._highs: dict[str, list[float]] = {}
        self._lows: dict[str, list[float]] = {}
        self._closes: dict[str, list[float]] = {}
        self._in_position: dict[str, bool] = {}
        self._highest_since_entry: dict[str, float] = {}
        self._trailing_stop: dict[str, float] = {}
        self._atr: dict[str, float | None] = {}
        self._prev_tr: dict[str, float | None] = {}
        self._atr_initialized: dict[str, bool] = {}

    def _update_atr(self, ticker: str) -> float | None:
        """Compute ATR using Wilder's smoothing method.

        Returns None if fewer than ``atr_period + 1`` bars have been seen
        (need at least one true range value).
        """
        highs = self._highs[ticker]
        lows = self._lows[ticker]
        closes = self._closes[ticker]

        if len(closes) < 2:
            return None

        h = highs[-1]
        l = lows[-1]
        prev_c = closes[-2]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))

        if not self._atr_initialized[ticker]:
            if len(closes) < self.atr_period + 1:
                self._prev_tr[ticker] = tr
                return None
            # Seed: SMA of first atr_period true ranges
            total = 0.0
            for i in range(1, self.atr_period + 1):
                th = highs[i]
                tl = lows[i]
                tcp = closes[i - 1]
                total += max(th - tl, abs(th - tcp), abs(tl - tcp))
            atr = total / self.atr_period
            self._atr[ticker] = atr
            self._atr_initialized[ticker] = True
            return atr

        prev_atr = self._atr[ticker]
        if prev_atr is None:
            return None
        atr = (prev_atr * (self.atr_period - 1) + tr) / self.atr_period
        self._atr[ticker] = atr
        return atr

    def on_market(self, event: MarketEvent) -> SignalEvent | None:
        ticker = event.ticker
        if ticker not in self._highs:
            self._highs[ticker] = []
            self._lows[ticker] = []
            self._closes[ticker] = []
            self._in_position[ticker] = False
            self._highest_since_entry[ticker] = 0.0
            self._trailing_stop[ticker] = 0.0
            self._atr[ticker] = None
            self._prev_tr[ticker] = None
            self._atr_initialized[ticker] = False

        self._highs[ticker].append(event.high)
        self._lows[ticker].append(event.low)
        self._closes[ticker].append(event.close)

        atr = self._update_atr(ticker)

        if not self._in_position[ticker]:
            if len(self._highs[ticker]) < self.entry_period + 1:
                return None

            highs = self._highs[ticker]
            channel_high = max(highs[-(self.entry_period + 1) : -1])

            if event.close > channel_high:
                self._in_position[ticker] = True
                self._highest_since_entry[ticker] = event.high

                if atr is not None and atr > 0:
                    self._trailing_stop[ticker] = event.high - (
                        self.atr_multiple * atr
                    )
                else:
                    self._trailing_stop[ticker] = 0.0

                strength = 0.5
                if atr is not None and atr > 0:
                    strength = min(
                        (event.close - self._trailing_stop[ticker])
                        / event.close,
                        1.0,
                    )

                return SignalEvent(
                    timestamp=event.timestamp,
                    ticker=ticker,
                    direction=SignalDirection.BUY,
                    strength=round(max(strength, 0.01), 4),
                    strategy_id=self.strategy_id,
                    metadata={
                        "trailing_stop": round(self._trailing_stop[ticker], 4),
                        "atr": round(atr, 4) if atr is not None else None,
                        "highest_since_entry": round(
                            self._highest_since_entry[ticker], 4
                        ),
                        "entry_period": self.entry_period,
                        "atr_period": self.atr_period,
                        "atr_multiple": self.atr_multiple,
                    },
                )

        else:
            self._highest_since_entry[ticker] = max(
                self._highest_since_entry[ticker], event.high
            )

            if atr is not None and atr > 0:
                new_stop = self._highest_since_entry[ticker] - (
                    self.atr_multiple * atr
                )
                self._trailing_stop[ticker] = max(
                    self._trailing_stop[ticker], new_stop
                )

            if (
                atr is not None
                and self._trailing_stop[ticker] > 0
                and event.close < self._trailing_stop[ticker]
            ):
                self._in_position[ticker] = False
                strength = min(
                    (self._trailing_stop[ticker] - event.close) / event.close,
                    1.0,
                )
                return SignalEvent(
                    timestamp=event.timestamp,
                    ticker=ticker,
                    direction=SignalDirection.SELL,
                    strength=round(max(strength, 0.01), 4),
                    strategy_id=self.strategy_id,
                    metadata={
                        "trailing_stop": round(self._trailing_stop[ticker], 4),
                        "atr": round(atr, 4),
                        "highest_since_entry": round(
                            self._highest_since_entry[ticker], 4
                        ),
                        "entry_period": self.entry_period,
                        "atr_period": self.atr_period,
                        "atr_multiple": self.atr_multiple,
                    },
                )

        return None
