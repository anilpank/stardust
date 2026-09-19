"""
Strategy base class and implementations.

Strategies consume MarketEvents and produce SignalEvents.
Each strategy is stateful — it maintains internal state (e.g., price history,
indicator values) across calls to on_market().
"""

import math
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


class DualMomentumStrategy(Strategy):
    """Dual Momentum strategy (absolute + relative momentum).

    Combines time-series (absolute) momentum with cross-sectional (relative)
    momentum against a benchmark:

    - BUY when flat and the ticker's lookback return is positive (absolute
      momentum) AND exceeds the benchmark's lookback return (relative momentum).
    - SELL when in position and either condition fails: the ticker's lookback
      return drops to zero or below, or falls to the benchmark's level or below.

    The benchmark price series is learned from the event stream itself — include
    the benchmark ticker (default SPY) in your ticker list. No signals are ever
    generated for the benchmark itself; it only provides reference data.

    Because bars arrive in strict chronological order, only past data is used.
    If the benchmark's same-date bar has not been delivered yet (tickers are
    processed alphabetically within a date), the latest available benchmark
    close is used — still strictly historical.

    Requires ``lookback + 1`` bars of history for both the ticker and the
    benchmark before producing any signals. If the benchmark never appears,
    the strategy stays silent (no signals, no errors).
    """

    def __init__(
        self,
        lookback: int = 126,
        benchmark_ticker: str = "SPY",
        strength_scale: float = 5.0,
        strategy_id: str = "dual_momentum",
    ) -> None:
        self.lookback = lookback
        self.benchmark_ticker = benchmark_ticker
        self.strength_scale = strength_scale
        self.strategy_id = strategy_id
        self._closes: dict[str, list[float]] = {}
        self._in_position: dict[str, bool] = {}

    def _momentum(self, closes: list[float]) -> float:
        """Total return over the lookback window (latest vs N bars ago)."""
        return closes[-1] / closes[-1 - self.lookback] - 1.0

    def on_market(self, event: MarketEvent) -> SignalEvent | None:
        ticker = event.ticker
        if ticker not in self._closes:
            self._closes[ticker] = []
            self._in_position[ticker] = False

        self._closes[ticker].append(event.close)

        # The benchmark itself is never traded — reference data only.
        if ticker == self.benchmark_ticker:
            return None

        bench_closes = self._closes.get(self.benchmark_ticker)
        if (
            len(self._closes[ticker]) < self.lookback + 1
            or bench_closes is None
            or len(bench_closes) < self.lookback + 1
        ):
            return None

        momentum = self._momentum(self._closes[ticker])
        bench_momentum = self._momentum(bench_closes)

        metadata = {
            "momentum": round(momentum, 6),
            "benchmark_momentum": round(bench_momentum, 6),
            "benchmark_ticker": self.benchmark_ticker,
            "lookback": self.lookback,
        }

        if not self._in_position[ticker]:
            if momentum > 0.0 and momentum > bench_momentum:
                self._in_position[ticker] = True
                metadata["absolute_pass"] = True
                metadata["relative_pass"] = True
                strength = min(
                    (momentum - bench_momentum) * self.strength_scale, 1.0
                )
                return SignalEvent(
                    timestamp=event.timestamp,
                    ticker=ticker,
                    direction=SignalDirection.BUY,
                    strength=round(max(strength, 0.01), 4),
                    strategy_id=self.strategy_id,
                    metadata=metadata,
                )
        else:
            if momentum <= 0.0 or momentum <= bench_momentum:
                self._in_position[ticker] = False
                metadata["absolute_pass"] = momentum > 0.0
                metadata["relative_pass"] = momentum > bench_momentum
                deficit = max(0.0, bench_momentum) - momentum
                strength = min(deficit * self.strength_scale, 1.0)
                return SignalEvent(
                    timestamp=event.timestamp,
                    ticker=ticker,
                    direction=SignalDirection.SELL,
                    strength=round(max(strength, 0.01), 4),
                    strategy_id=self.strategy_id,
                    metadata=metadata,
                )

        return None


FACTOR_NAMES = ("momentum", "low_vol", "trend", "reversal")


class FactorScoringStrategy(Strategy):
    """Multi-factor composite score strategy.

    Combines four price-derived factors — momentum, low volatility, trend, and
    (optional) short-term reversal — into a single composite score in [0, 1].
    Each factor is normalized against the ticker's OWN rolling history (a
    trailing z-score, clipped to [-2, 2] and scaled to [0, 1]), so the score
    depends only on the ticker's past data. That makes it lookahead-safe within
    the event stream (no reliance on the same-day cross-section) while still
    being comparable across tickers for ranking in reports.

    Trading rules per ticker (a state machine, like Dual Momentum):

    - Flat → BUY when the composite score *crosses up* through ``entry_threshold``
      and, if ``absolute_momentum`` is set, the ticker's lookback return is positive.
    - In position → SELL when the composite score *crosses down* through
      ``exit_threshold`` or the absolute-momentum gate fails.
    - The entry/exit hysteresis (default 0.60 > 0.50) reduces whipsaw.

    ``weights`` must sum to a positive value; they are normalized to sum to 1.
    Unknown factor names raise ValueError. The absolute-momentum gate always uses
    the ``momentum_lookback`` return regardless of whether momentum is a weighted
    factor, so it keeps working even when momentum's weight is zero.

    Requires ``max(momentum, volatility, trend_slow, reversion lookbacks) + window``
    bars of history before producing any signals (every weighted factor needs a
    full rolling normalization window).
    """

    def __init__(
        self,
        momentum_lookback: int = 126,
        volatility_lookback: int = 60,
        trend_fast: int = 20,
        trend_slow: int = 60,
        reversion_lookback: int = 10,
        window: int = 252,
        weights: dict[str, float] | None = None,
        entry_threshold: float = 0.60,
        exit_threshold: float = 0.50,
        absolute_momentum: bool = True,
        strategy_id: str = "factor_scoring",
    ) -> None:
        self.momentum_lookback = momentum_lookback
        self.volatility_lookback = volatility_lookback
        self.trend_fast = trend_fast
        self.trend_slow = trend_slow
        self.reversion_lookback = reversion_lookback
        self.window = window
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.absolute_momentum = absolute_momentum
        self.strategy_id = strategy_id

        weights = weights or {
            "momentum": 0.4,
            "low_vol": 0.3,
            "trend": 0.3,
            "reversal": 0.0,
        }
        unknown = set(weights) - set(FACTOR_NAMES)
        if unknown:
            raise ValueError(
                f"Unknown factor(s) {', '.join(sorted(unknown))}. "
                f"Known factors: {', '.join(FACTOR_NAMES)}"
            )
        total = sum(weights.get(f, 0.0) for f in FACTOR_NAMES)
        if total <= 0:
            raise ValueError("Factor weights must sum to a positive value")
        self.weights = {f: weights.get(f, 0.0) / total for f in FACTOR_NAMES}
        self._active = [f for f in FACTOR_NAMES if self.weights[f] > 0]

        # Leading lookback is the largest window any raw factor needs before it
        # can be computed — determines how much close history to keep.
        self._max_lookback = max(
            momentum_lookback, volatility_lookback, trend_slow, reversion_lookback
        )
        self._max_closes = self.window + self._max_lookback + 1

        self._closes: dict[str, list[float]] = {}
        self._raw: dict[str, dict[str, list[float]]] = {}
        self._prev_score: dict[str, float | None] = {}
        self._in_position: dict[str, bool] = {}

    # ---- factor signals (pure functions of the ticker's close history) ----

    def _momentum_return(self, closes: list[float]) -> float | None:
        """Total return over ``momentum_lookback`` bars, or None if unknown."""
        if len(closes) < self.momentum_lookback + 1:
            return None
        return closes[-1] / closes[-1 - self.momentum_lookback] - 1.0

    def _raw_factor(self, factor: str, closes: list[float]) -> float | None:
        """Compute a factor's raw value from the close history."""
        if factor == "momentum":
            return self._momentum_return(closes)
        if factor == "trend":
            if len(closes) < self.trend_slow + 1:
                return None
            fast = sum(closes[-self.trend_fast :]) / self.trend_fast
            slow = sum(closes[-self.trend_slow :]) / self.trend_slow
            return (fast - slow) / slow
        if factor == "reversal":
            if len(closes) < self.reversion_lookback + 1:
                return None
            ret = closes[-1] / closes[-1 - self.reversion_lookback] - 1.0
            return -ret
        if factor == "low_vol":
            if len(closes) < self.volatility_lookback + 1:
                return None
            window_closes = closes[-self.volatility_lookback - 1 :]
            n = len(window_closes) - 1
            log_rets = [
                math.log(window_closes[i] / window_closes[i - 1])
                for i in range(1, len(window_closes))
            ]
            mean = sum(log_rets) / n
            var = sum((r - mean) ** 2 for r in log_rets) / (n - 1)
            return -math.sqrt(max(var, 0.0))
        raise ValueError(f"Unknown factor '{factor}'")

    @staticmethod
    def _normalize(value: float, history: list[float]) -> float:
        """Map a value to [0, 1] via a trailing z-score clipped to [-2, 2].

        ``history`` is the trailing window including the current value. With a
        degenerate window (std ~ 0) the factor is treated as neutral (0.5).
        """
        n = len(history)
        if n < 2:
            return 0.5
        mean = sum(history) / n
        variance = sum((v - mean) ** 2 for v in history) / (n - 1)
        std = math.sqrt(variance)
        if std < 1e-9:
            return 0.5
        z = max(-2.0, min(2.0, (value - mean) / std))
        return (z + 2.0) / 4.0

    def _factor_component(self, ticker: str, factor: str) -> tuple[float, float]:
        """Return (z, u) for a factor where u = normalized [0, 1] component."""
        hist = self._raw[ticker][factor]
        value = hist[-1]
        n = len(hist)
        if n < 2:
            return 0.0, 0.5
        mean = sum(hist) / n
        variance = sum((v - mean) ** 2 for v in hist) / (n - 1)
        std = math.sqrt(variance)
        if std < 1e-9:
            return 0.0, 0.5
        z = max(-2.0, min(2.0, (value - mean) / std))
        return z, (z + 2.0) / 4.0

    def _composite(self, ticker: str) -> float | None:
        """Weighted sum of normalized active factors, or None before warmup."""
        for factor in self._active:
            if len(self._raw[ticker][factor]) < self.window:
                return None
        return sum(
            self.weights[f] * self._factor_component(ticker, f)[1]
            for f in self._active
        )

    # ---- event handler ----

    def on_market(self, event: MarketEvent) -> SignalEvent | None:
        ticker = event.ticker
        if ticker not in self._closes:
            self._closes[ticker] = []
            self._raw[ticker] = {f: [] for f in FACTOR_NAMES}
            self._prev_score[ticker] = None
            self._in_position[ticker] = False

        closes = self._closes[ticker]
        closes.append(event.close)
        if len(closes) > self._max_closes:
            del closes[: len(closes) - self._max_closes]

        for factor in self._active:
            value = self._raw_factor(factor, closes)
            if value is None:
                continue
            hist = self._raw[ticker][factor]
            hist.append(value)
            if len(hist) > self.window:
                del hist[0]

        score = self._composite(ticker)
        if score is None:
            self._prev_score[ticker] = None
            return None

        prev = self._prev_score[ticker]
        self._prev_score[ticker] = score

        momentum_ret = self._momentum_return(closes)
        # The absolute-momentum gate uses the raw lookback return, independent
        # of whether momentum is a weighted factor.
        abs_pass = (
            not self.absolute_momentum
            or (momentum_ret is not None and momentum_ret > 0.0)
        )

        if not self._in_position[ticker]:
            if (
                prev is not None
                and score > self.entry_threshold
                and prev <= self.entry_threshold
                and abs_pass
            ):
                self._in_position[ticker] = True
                strength = min(
                    (score - self.entry_threshold) / (1.0 - self.entry_threshold),
                    1.0,
                )
                return SignalEvent(
                    timestamp=event.timestamp,
                    ticker=ticker,
                    direction=SignalDirection.BUY,
                    strength=round(max(strength, 0.01), 4),
                    strategy_id=self.strategy_id,
                    metadata=self._metadata(ticker, score, momentum_ret, True, abs_pass),
                )
        else:
            exit_on_score = (
                prev is not None
                and score < self.exit_threshold
                and prev >= self.exit_threshold
            )
            exit_on_gate = (
                self.absolute_momentum and momentum_ret is not None and momentum_ret <= 0.0
            )
            if exit_on_score or exit_on_gate:
                self._in_position[ticker] = False
                if exit_on_score:
                    strength = min(
                        (self.exit_threshold - score) / self.exit_threshold, 1.0
                    )
                else:
                    strength = min(-momentum_ret * 5.0, 1.0)
                return SignalEvent(
                    timestamp=event.timestamp,
                    ticker=ticker,
                    direction=SignalDirection.SELL,
                    strength=round(max(strength, 0.01), 4),
                    strategy_id=self.strategy_id,
                    metadata=self._metadata(ticker, score, momentum_ret, False, abs_pass),
                )

        return None

    def _metadata(
        self,
        ticker: str,
        score: float,
        momentum_ret: float | None,
        entering: bool,
        abs_pass: bool,
    ) -> dict:
        components = {}
        for factor in self._active:
            z, u = self._factor_component(ticker, factor)
            components[factor] = {"z": round(z, 4), "u": round(u, 4)}
        return {
            "score": round(score, 4),
            "entry_threshold": self.entry_threshold,
            "exit_threshold": self.exit_threshold,
            "momentum_return": (
                round(momentum_ret, 6) if momentum_ret is not None else None
            ),
            "absolute_momentum": self.absolute_momentum,
            "absolute_pass": abs_pass,
            "factors": components,
            "weights": {f: round(w, 4) for f, w in self.weights.items()},
        }
