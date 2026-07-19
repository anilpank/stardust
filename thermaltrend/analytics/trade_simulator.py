"""Simulate trades from signal events.

Converts raw SignalEvents into simulated Trades with PnL, using:
- $10K fixed position sizing
- Entry/exit at next day's open (no lookahead bias)
- Optional ATR-based stop loss (2x ATR, 14-day lookback)
- Unmatched BUY signals closed at last available price with "data_end" flag
"""

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from thermaltrend.core.events import SignalDirection, SignalEvent


@dataclass
class Trade:
    """A completed (or data-end-closed) simulated trade."""

    ticker: str
    entry_date: datetime
    entry_price: float
    exit_date: datetime
    exit_price: float
    direction: SignalDirection
    pnl: float
    pnl_pct: float
    holding_days: int
    exit_reason: str  # "signal" | "stop_loss" | "data_end"
    strategy_id: str
    shares: int = 0
    stop_price: float = 0.0


@dataclass
class TradeSimulator:
    """Simulates trades from a list of SignalEvents.

    Args:
        position_size: Dollar amount per trade (default $10,000).
        stop_atr_multiple: ATR multiplier for stop loss. 0.0 disables.
        atr_lookback: Number of days for ATR calculation (default 14).
        use_next_day_open: If True, enter/exit at next day's open price.
    """

    position_size: float = 10_000.0
    stop_atr_multiple: float = 2.0
    atr_lookback: int = 14
    use_next_day_open: bool = True

    def simulate(
        self,
        signals: list[SignalEvent],
        price_data: pd.DataFrame,
    ) -> list[Trade]:
        """Simulate trades from signals against price data.

        Args:
            signals: List of SignalEvents from the engine.
            price_data: DataFrame with MultiIndex (date, ticker) and
                       columns Open, High, Low, Close, Volume.

        Returns:
            List of completed Trade objects.
        """
        if not signals or price_data.empty:
            return []

        signals_by_ticker = self._group_signals_by_ticker(signals)
        all_trades: list[Trade] = []

        for ticker, ticker_signals in signals_by_ticker.items():
            ticker_data = self._get_ticker_data(price_data, ticker)
            if ticker_data.empty:
                continue
            trades = self._simulate_ticker(ticker_signals, ticker_data, ticker)
            all_trades.extend(trades)

        return sorted(all_trades, key=lambda t: t.entry_date)

    def _group_signals_by_ticker(
        self, signals: list[SignalEvent]
    ) -> dict[str, list[SignalEvent]]:
        """Group signals by ticker, preserving chronological order."""
        grouped: dict[str, list[SignalEvent]] = {}
        for sig in signals:
            grouped.setdefault(sig.ticker, []).append(sig)
        return grouped

    def _get_ticker_data(self, price_data: pd.DataFrame, ticker: str) -> pd.DataFrame:
        """Extract data for a single ticker from the multi-indexed DataFrame."""
        try:
            return price_data.xs(ticker, level="ticker")
        except KeyError:
            return pd.DataFrame()

    def _compute_atr(self, ticker_data: pd.DataFrame) -> pd.Series:
        """Compute ATR (Average True Range) for a ticker.

        ATR = SMA of True Range over `atr_lookback` days.
        True Range = max(H-L, |H-prev_C|, |L-prev_C|).
        """
        high = ticker_data["High"]
        low = ticker_data["Low"]
        prev_close = ticker_data["Close"].shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        return true_range.rolling(window=self.atr_lookback, min_periods=1).mean()

    def _find_next_date(
        self, ticker_data: pd.DataFrame, current_date: pd.Timestamp
    ) -> pd.Timestamp | None:
        """Find the next trading date after current_date."""
        future_dates = ticker_data.index[ticker_data.index > current_date]
        if len(future_dates) == 0:
            return None
        return future_dates[0]

    def _get_price_on_date(
        self, ticker_data: pd.DataFrame, date: pd.Timestamp, field: str = "Open"
    ) -> float | None:
        """Get a price field on a specific date."""
        if date in ticker_data.index:
            return float(ticker_data.loc[date, field])
        return None

    def _simulate_ticker(
        self,
        signals: list[SignalEvent],
        ticker_data: pd.DataFrame,
        ticker: str,
    ) -> list[Trade]:
        """Simulate trades for a single ticker's signals."""
        trades: list[Trade] = []
        atr = self._compute_atr(ticker_data) if self.stop_atr_multiple > 0 else None
        dates = ticker_data.index

        open_position: dict | None = None

        for signal in signals:
            signal_date = pd.Timestamp(signal.timestamp.date())

            if signal.direction == SignalDirection.BUY and open_position is None:
                entry_date = self._find_next_date(ticker_data, signal_date)
                if entry_date is None:
                    continue

                entry_price = self._get_price_on_date(ticker_data, entry_date, "Open")
                if entry_price is None:
                    continue

                shares = int(self.position_size / entry_price)
                if shares <= 0:
                    continue

                stop_price = 0.0
                if atr is not None and signal_date in atr.index:
                    current_atr = atr.loc[signal_date]
                    if not np.isnan(current_atr) and current_atr > 0:
                        stop_price = entry_price - (self.stop_atr_multiple * current_atr)

                open_position = {
                    "ticker": ticker,
                    "entry_date": entry_date.to_pydatetime(),
                    "entry_price": entry_price,
                    "shares": shares,
                    "stop_price": stop_price,
                    "strategy_id": signal.strategy_id,
                }

            elif signal.direction == SignalDirection.SELL and open_position is not None:
                exit_date = self._find_next_date(ticker_data, signal_date)
                if exit_date is None:
                    exit_date = ticker_data.index[-1]
                    exit_price = self._get_price_on_date(
                        ticker_data, exit_date, "Close"
                    )
                    trade = self._close_trade(
                        open_position, exit_date, exit_price, "data_end"
                    )
                    trades.append(trade)
                    open_position = None
                    continue

                exit_price = self._get_price_on_date(ticker_data, exit_date, "Open")
                if exit_price is None:
                    continue

                if (
                    open_position["stop_price"] > 0
                    and self._check_stop_hit(
                        ticker_data, open_position, signal_date
                    )
                ):
                    stop_date, stop_price = self._find_stop_exit(
                        ticker_data, open_position, signal_date
                    )
                    if stop_date is not None:
                        trade = self._close_trade(
                            open_position, stop_date, stop_price, "stop_loss"
                        )
                        trades.append(trade)
                        open_position = None
                        continue

                trade = self._close_trade(
                    open_position, exit_date.to_pydatetime(), exit_price, "signal"
                )
                trades.append(trade)
                open_position = None

        if open_position is not None:
            last_date = dates[-1]
            last_close = self._get_price_on_date(ticker_data, last_date, "Close")
            if last_close is not None:
                trade = self._close_trade(
                    open_position, last_date.to_pydatetime(), last_close, "data_end"
                )
                trades.append(trade)

        return trades

    def _check_stop_hit(
        self,
        ticker_data: pd.DataFrame,
        position: dict,
        signal_date: pd.Timestamp,
    ) -> bool:
        """Check if the stop loss was hit between entry and signal_date."""
        stop = position["stop_price"]
        entry_date = pd.Timestamp(position["entry_date"])
        window = ticker_data.loc[entry_date:signal_date]
        return bool((window["Low"] <= stop).any())

    def _find_stop_exit(
        self,
        ticker_data: pd.DataFrame,
        position: dict,
        signal_date: pd.Timestamp,
    ) -> tuple[datetime | None, float]:
        """Find the first date and price where stop was hit."""
        stop = position["stop_price"]
        entry_date = pd.Timestamp(position["entry_date"])
        window = ticker_data.loc[entry_date:signal_date]

        for date, row in window.iterrows():
            if row["Low"] <= stop:
                exit_price = max(stop, row["Open"])
                return date.to_pydatetime(), exit_price

        return None, 0.0

    def _close_trade(
        self,
        position: dict,
        exit_date: datetime,
        exit_price: float,
        exit_reason: str,
    ) -> Trade:
        """Create a Trade from an open position and exit details."""
        entry_price = position["entry_price"]
        shares = position["shares"]
        pnl = (exit_price - entry_price) * shares
        pnl_pct = (exit_price - entry_price) / entry_price if entry_price > 0 else 0.0

        entry_dt = pd.Timestamp(position["entry_date"])
        exit_dt = pd.Timestamp(exit_date)
        holding_days = max((exit_dt - entry_dt).days, 0)

        return Trade(
            ticker=position["ticker"],
            entry_date=position["entry_date"],
            entry_price=entry_price,
            exit_date=exit_date,
            exit_price=exit_price,
            direction=SignalDirection.BUY,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 6),
            holding_days=holding_days,
            exit_reason=exit_reason,
            strategy_id=position["strategy_id"],
            shares=shares,
            stop_price=position["stop_price"],
        )
