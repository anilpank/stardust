"""
Event types and event queue for the event-driven engine.

Events flow chronologically:
    MarketEvent → SignalEvent → (future: OrderEvent → FillEvent)

The EventQueue enforces strict sequential processing — no future data leaks.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4


class EventType(Enum):
    """Types of events in the system."""

    MARKET = "market"
    SIGNAL = "signal"


class SignalDirection(Enum):
    """Direction of a trading signal."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class MarketEvent:
    """New market bar arrived (OHLCV + metadata).

    Created by the DataEngine when a new bar is read from the DataFeed.
    This is the input that triggers strategy evaluation.
    """

    timestamp: datetime
    ticker: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class SignalEvent:
    """Strategy generated a trading signal.

    Created by a Strategy when it detects an opportunity.
    Strength indicates conviction (0.0 = weak, 1.0 = maximum).
    """

    timestamp: datetime
    ticker: str
    direction: SignalDirection
    strength: float
    strategy_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    id: UUID = field(default_factory=uuid4)


class EventQueue:
    """FIFO event queue backed by collections.deque.

    Events are processed in the order they are put() in.
    deque.popleft() is O(1), making this efficient for high-throughput
    event processing.
    """

    def __init__(self) -> None:
        self._queue: deque[MarketEvent | SignalEvent] = deque()

    def put(self, event: MarketEvent | SignalEvent) -> None:
        """Add an event to the back of the queue."""
        self._queue.append(event)

    def get(self) -> MarketEvent | SignalEvent | None:
        """Remove and return the front event, or None if empty."""
        if self._queue:
            return self._queue.popleft()
        return None

    def is_empty(self) -> bool:
        """Return True if the queue has no events."""
        return len(self._queue) == 0

    def __len__(self) -> int:
        return len(self._queue)

    def __repr__(self) -> str:
        return f"EventQueue(len={len(self)})"
