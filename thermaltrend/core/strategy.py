"""
Strategy base class and initial implementations.

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
