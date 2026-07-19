"""
Main event-driven data engine.

The engine orchestrates the flow:
    DataFeed → MarketEvent → Strategy → SignalEvent

Events are processed in strict chronological order to prevent lookahead bias.
"""

from thermaltrend.core.events import EventQueue, MarketEvent, SignalEvent
from thermaltrend.core.strategy import Strategy
from thermaltrend.feed import DataFeed


class DataEngine:
    """Event-driven engine that feeds market data through a strategy.

    Iterates through the DataFeed date by date, converts bars to MarketEvents,
    and collects SignalEvents produced by the strategy.
    """

    def __init__(self, feed: DataFeed, strategy: Strategy) -> None:
        self.feed = feed
        self.strategy = strategy
        self._queue = EventQueue()
        self._signals: list[SignalEvent] = []

    def run(self) -> list[SignalEvent]:
        """Run the engine on all dates in the data feed.

        Returns all SignalEvents produced by the strategy, sorted by
        timestamp then ticker.
        """
        self._signals = []

        for date in self.feed.dates:
            bars = self.feed.get_bars_for_date(date)
            for bar in bars:
                event = MarketEvent(
                    timestamp=bar.date,
                    ticker=bar.ticker,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                )
                self._queue.put(event)

            while not self._queue.is_empty():
                event = self._queue.get()
                if isinstance(event, MarketEvent):
                    signal = self.strategy.on_market(event)
                    if signal is not None:
                        self._signals.append(signal)

        return sorted(self._signals, key=lambda s: (s.timestamp, s.ticker))

    @property
    def signals(self) -> list[SignalEvent]:
        """Return signals from the most recent run()."""
        return list(self._signals)
