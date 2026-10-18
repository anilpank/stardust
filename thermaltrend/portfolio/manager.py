"""Shared-cash portfolio simulation.

Generalizes the per-ticker TradeSimulator into one account with real
constraints: a single cash balance, a principal sleeve floor, an exposure
band, per-trade risk sizing with ATR stops, a regime gate, a drawdown
breaker off the equity high-water mark, a monthly profit ratchet, and a
core (index-timing) lane alongside satellite stock positions.

Conventions match TradeSimulator: signals fill at the next day's open and
``slippage_bps`` is charged on every open-based fill. Stop exits fill at
``min(stop, open)`` so a gap below the stop fills at the gapped open rather
than the (unreachable) stop price. Positions still open when their data (or
the backtest window) ends are force-closed at the last close with
``exit_reason="data_end"``.
"""

import math
from dataclasses import dataclass

import pandas as pd

from thermaltrend.analytics.metrics import compute_aggregate_metrics
from thermaltrend.analytics.regime import MarketRegime, classify_regime
from thermaltrend.analytics.trade_simulator import Trade
from thermaltrend.core.events import SignalDirection, SignalEvent
from thermaltrend.portfolio.config import PortfolioConfig
from thermaltrend.portfolio.risk import (
    Breaker,
    bank_amount,
    entries_allowed,
)
from thermaltrend.portfolio.sizing import size_satellite_entry

CORE_LANE = "core"
SATELLITE_LANE = "satellite"
REASON_DATA_END = "data_end"
REASON_NO_PRICE = "no_price"
REASON_BREAKER = "breaker"
REASON_BEAR_REGIME = "bear_regime"
REASON_ALREADY_HELD = "already_held"
REASON_CORE_TARGET_SMALL = "core_target_too_small"


def _valid_price(value) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def _row_on(df: pd.DataFrame, date: pd.Timestamp) -> pd.Series | None:
    if date not in df.index:
        return None
    row = df.loc[date]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    return row


def _price_on(df: pd.DataFrame, date: pd.Timestamp, field_name: str) -> float | None:
    row = _row_on(df, date)
    if row is None:
        return None
    return _valid_price(row[field_name])


def _compute_atr(df: pd.DataFrame, lookback: int) -> pd.Series:
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return true_range.rolling(window=lookback, min_periods=1).mean()


@dataclass
class Position:
    ticker: str
    lane: str
    shares: int
    entry_date: pd.Timestamp
    entry_price: float
    stop_price: float
    strategy_id: str
    last_close: float


@dataclass(frozen=True)
class Order:
    ticker: str
    side: str
    lane: str
    strategy_id: str = ""
    signal_date: pd.Timestamp | None = None
    stop_distance: float = 0.0


@dataclass
class PortfolioResult:
    config: PortfolioConfig
    trades: list[Trade]
    equity_curve: pd.Series
    daily: pd.DataFrame
    rejected: list[dict]
    ratchet_events: list[dict]
    breaker_events: list[dict]
    core_history: list[dict]
    metrics: dict


class PortfolioManager:
    def __init__(self, config: PortfolioConfig | None = None):
        self.config = config or PortfolioConfig()

    def run(
        self,
        signals: list[SignalEvent] | None,
        price_data: pd.DataFrame,
    ) -> PortfolioResult:
        cfg = self.config
        if price_data is None or len(price_data) == 0:
            raise ValueError("price_data is empty")

        frames: dict[str, pd.DataFrame] = {}
        for ticker in price_data.index.get_level_values("ticker").unique():
            frames[str(ticker)] = price_data.xs(ticker, level="ticker").sort_index()
        if cfg.core_ticker not in frames:
            raise ValueError(
                f"core ticker '{cfg.core_ticker}' missing from price data"
            )

        spy = frames[cfg.core_ticker]
        dates = pd.DatetimeIndex(
            sorted(price_data.index.get_level_values("date").unique())
        )
        n_dates = len(dates)

        regimes = classify_regime(
            spy["Close"],
            lookback_days=cfg.regime_lookback_days,
            bull_threshold=cfg.regime_bull_threshold,
            bear_threshold=cfg.regime_bear_threshold,
        )
        regime_at = regimes.reindex(dates, method="ffill")
        spy_close = spy["Close"].reindex(dates, method="ffill")

        atr_cache: dict[str, pd.Series] = {}

        def atr_for(ticker: str) -> pd.Series:
            if ticker not in atr_cache:
                atr_cache[ticker] = _compute_atr(frames[ticker], cfg.atr_lookback)
            return atr_cache[ticker]

        sig_items: list[tuple[pd.Timestamp, SignalEvent]] = []
        for sig in signals or []:
            if sig.ticker == cfg.core_ticker or sig.ticker not in frames:
                continue
            sig_items.append((pd.Timestamp(sig.timestamp).normalize(), sig))
        sig_items.sort(key=lambda item: item[0])
        sig_ptr = 0

        positions: dict[str, Position] = {}
        pending: list[Order] = []
        trades: list[Trade] = []
        rejected: list[dict] = []
        ratchet_events: list[dict] = []
        breaker_events: list[dict] = []
        core_history: list[dict] = []
        daily_rows: list[dict] = []

        slip = cfg.slippage_bps / 10_000.0
        cash = cfg.capital
        invested_close = 0.0
        equity_close = cfg.capital
        hwm = cfg.capital
        last_banked_hwm = cfg.capital
        principal_target = cfg.principal_start
        breaker = Breaker()
        regime_prev: str | None = None

        for i, date in enumerate(dates):
            sold_value = 0.0

            sells = [o for o in pending if o.side == "sell"]
            buys = [o for o in pending if o.side == "buy"]
            pending = []

            for order in sells:
                pos = positions.get(order.ticker)
                if pos is None:
                    continue
                fill_open = _price_on(frames[order.ticker], date, "Open")
                if fill_open is None:
                    rejected.append(
                        self._rejection(date, order, REASON_NO_PRICE)
                    )
                    continue
                fill = fill_open * (1.0 - slip)
                cash += pos.shares * fill
                sold_value += pos.shares * pos.last_close
                trades.append(
                    self._close_trade(pos, date, fill, "signal")
                )
                del positions[order.ticker]

            for order in buys:
                if order.ticker in positions:
                    rejected.append(
                        self._rejection(date, order, REASON_ALREADY_HELD)
                    )
                    continue
                if not entries_allowed(breaker.tripped, regime_prev):
                    reason = REASON_BREAKER if breaker.tripped else REASON_BEAR_REGIME
                    rejected.append(self._rejection(date, order, reason))
                    continue
                fill_open = _price_on(frames[order.ticker], date, "Open")
                if fill_open is None:
                    rejected.append(
                        self._rejection(date, order, REASON_NO_PRICE)
                    )
                    continue
                fill = fill_open * (1.0 + slip)
                invested_ref = invested_close - sold_value

                if order.lane == CORE_LANE:
                    shares, reason = self._core_shares(
                        fill=fill,
                        equity_ref=equity_close,
                        invested_ref=invested_ref,
                        cash=cash,
                        principal_target=principal_target,
                        config=cfg,
                    )
                    if shares < 1:
                        rejected.append(self._rejection(date, order, reason))
                        continue
                    cash -= shares * fill
                    positions[order.ticker] = Position(
                        ticker=order.ticker,
                        lane=CORE_LANE,
                        shares=shares,
                        entry_date=date,
                        entry_price=fill,
                        stop_price=0.0,
                        strategy_id=CORE_LANE,
                        last_close=_price_on(frames[order.ticker], date, "Close")
                        or fill,
                    )
                    continue

                decision = size_satellite_entry(
                    equity=equity_close,
                    cash=cash,
                    invested=invested_ref,
                    risk_per_trade=cfg.risk_per_trade,
                    stop_distance=order.stop_distance,
                    entry_price=fill,
                    max_position_pct=cfg.max_position_pct,
                    min_position_notional=cfg.min_position_notional,
                    band_max=cfg.band_max,
                    principal_target=principal_target,
                    positions_used=sum(
                        1 for p in positions.values() if p.lane == SATELLITE_LANE
                    ),
                    max_positions=cfg.max_positions,
                )
                if not decision.ok:
                    rejected.append(self._rejection(date, order, decision.reason))
                    continue
                cash -= decision.notional
                positions[order.ticker] = Position(
                    ticker=order.ticker,
                    lane=SATELLITE_LANE,
                    shares=decision.shares,
                    entry_date=date,
                    entry_price=decision.entry_price,
                    stop_price=decision.stop_price,
                    strategy_id=order.strategy_id,
                    last_close=_price_on(frames[order.ticker], date, "Close")
                    or decision.entry_price,
                )

            for ticker, pos in list(positions.items()):
                if pos.lane != SATELLITE_LANE or pos.stop_price <= 0:
                    continue
                row = _row_on(frames[ticker], date)
                if row is None:
                    continue
                low = _valid_price(row["Low"])
                if low is None or low > pos.stop_price:
                    continue
                open_px = _valid_price(row["Open"])
                exit_base = (
                    min(pos.stop_price, open_px)
                    if open_px is not None
                    else pos.stop_price
                )
                fill = exit_base * (1.0 - slip)
                cash += pos.shares * fill
                trades.append(self._close_trade(pos, date, fill, "stop_loss"))
                del positions[ticker]

            while sig_ptr < len(sig_items) and sig_items[sig_ptr][0] <= date:
                sig_date, sig = sig_items[sig_ptr]
                sig_ptr += 1
                pos = positions.get(sig.ticker)
                queued = any(
                    o.ticker == sig.ticker and o.side == sig.direction.value.lower()
                    for o in pending
                )
                if sig.direction == SignalDirection.SELL:
                    if pos is not None and pos.lane == SATELLITE_LANE and not queued:
                        pending.append(
                            Order(
                                ticker=sig.ticker,
                                side="sell",
                                lane=SATELLITE_LANE,
                                strategy_id=sig.strategy_id,
                                signal_date=sig_date,
                            )
                        )
                elif sig.direction == SignalDirection.BUY:
                    if pos is not None or queued:
                        continue
                    stop_distance = 0.0
                    if cfg.stop_atr_multiple > 0:
                        atr = atr_for(sig.ticker)
                        if sig_date in atr.index:
                            atr_value = _valid_price(atr.loc[sig_date])
                            if atr_value is not None:
                                stop_distance = cfg.stop_atr_multiple * atr_value
                    pending.append(
                        Order(
                            ticker=sig.ticker,
                            side="buy",
                            lane=SATELLITE_LANE,
                            strategy_id=sig.strategy_id,
                            signal_date=sig_date,
                            stop_distance=stop_distance,
                        )
                    )

            core_on = False
            momentum = None
            if cfg.core_enabled:
                current = _valid_price(spy_close.iloc[i]) if i < len(spy_close) else None
                past = (
                    _valid_price(spy_close.iloc[i - cfg.core_momentum_lookback])
                    if i >= cfg.core_momentum_lookback
                    else None
                )
                if current is not None and past is not None:
                    momentum = current / past - 1.0
                regime_now = (
                    regime_at.iloc[i].value
                    if isinstance(regime_at.iloc[i], MarketRegime)
                    else None
                )
                core_on = (
                    momentum is not None
                    and momentum > 0
                    and regime_now != MarketRegime.BEAR.value
                )
                core_held = (
                    cfg.core_ticker in positions
                    and positions[cfg.core_ticker].lane == CORE_LANE
                )
                if core_on and not core_held:
                    pending.append(
                        Order(
                            ticker=cfg.core_ticker,
                            side="buy",
                            lane=CORE_LANE,
                            strategy_id=CORE_LANE,
                            signal_date=date,
                        )
                    )
                elif not core_on and core_held:
                    pending.append(
                        Order(
                            ticker=cfg.core_ticker,
                            side="sell",
                            lane=CORE_LANE,
                            strategy_id=CORE_LANE,
                            signal_date=date,
                        )
                    )
                core_history.append(
                    {
                        "date": date,
                        "on": core_on,
                        "momentum": None if momentum is None else round(momentum, 6),
                        "regime": regime_now,
                    }
                )

            invested = 0.0
            core_invested = 0.0
            for ticker, pos in positions.items():
                row = _row_on(frames[ticker], date)
                if row is not None:
                    close_px = _valid_price(row["Close"])
                    if close_px is not None:
                        pos.last_close = close_px
                value = pos.shares * pos.last_close
                invested += value
                if pos.lane == CORE_LANE:
                    core_invested += value

            cash_close = cash
            equity_close = cash_close + invested
            invested_close = invested
            if equity_close > hwm:
                hwm = equity_close
            if breaker.update(equity_close, hwm, cfg):
                breaker_events.append(
                    {
                        "date": date,
                        "tripped": breaker.tripped,
                        "equity": round(equity_close, 2),
                        "hwm": round(hwm, 2),
                    }
                )

            regime_prev = (
                regime_at.iloc[i].value
                if isinstance(regime_at.iloc[i], MarketRegime)
                else None
            )

            last_of_month = (
                i + 1 == n_dates or dates[i + 1].month != date.month
            )
            if last_of_month:
                bank = bank_amount(equity_close, last_banked_hwm, cfg.ratchet_rate)
                if bank > 0:
                    principal_target = min(principal_target + bank, equity_close)
                    last_banked_hwm = equity_close
                    ratchet_events.append(
                        {
                            "date": date,
                            "bank": round(bank, 2),
                            "equity": round(equity_close, 2),
                            "principal_target": round(principal_target, 2),
                        }
                    )

            daily_rows.append(
                {
                    "date": date,
                    "equity": equity_close,
                    "cash": cash_close,
                    "invested": invested,
                    "core_invested": core_invested,
                    "satellite_invested": invested - core_invested,
                    "exposure": invested / equity_close if equity_close > 0 else 0.0,
                    "principal_target": principal_target,
                    "hwm": hwm,
                    "breaker": breaker.tripped,
                    "regime": regime_prev,
                }
            )

        last_date = dates[-1]
        for order in pending:
            rejected.append(self._rejection(last_date, order, REASON_DATA_END))
        for ticker, pos in list(positions.items()):
            exit_date = frames[ticker].index[-1]
            cash += pos.shares * pos.last_close
            trades.append(self._close_trade(pos, exit_date, pos.last_close, "data_end"))
            del positions[ticker]

        trades.sort(key=lambda t: pd.Timestamp(t.entry_date))
        equity_curve = self._build_equity_curve(dates, daily_rows, cfg.capital)
        daily = pd.DataFrame(daily_rows)
        metrics = self._compute_metrics(
            trades, equity_curve, daily, spy, dates, cfg, ratchet_events
        )
        return PortfolioResult(
            config=cfg,
            trades=trades,
            equity_curve=equity_curve,
            daily=daily,
            rejected=rejected,
            ratchet_events=ratchet_events,
            breaker_events=breaker_events,
            core_history=core_history,
            metrics=metrics,
        )

    @staticmethod
    def _core_shares(
        *,
        fill: float,
        equity_ref: float,
        invested_ref: float,
        cash: float,
        principal_target: float,
        config: PortfolioConfig,
    ) -> tuple[int, str]:
        target_value = equity_ref * config.core_target_weight
        shares = int(target_value / fill)
        if shares < 1:
            return 0, REASON_CORE_TARGET_SMALL
        band_room = equity_ref * config.band_max - invested_ref
        band_shares = int(band_room / fill)
        if band_shares < 1:
            return 0, "band_full"
        shares = min(shares, band_shares)
        cash_shares = int((cash - principal_target) / fill)
        if cash_shares < 1:
            return 0, "principal_floor"
        shares = min(shares, cash_shares)
        if shares < 1:
            return 0, "principal_floor"
        return shares, "ok"

    @staticmethod
    def _rejection(date: pd.Timestamp, order: Order, reason: str) -> dict:
        return {
            "date": date,
            "ticker": order.ticker,
            "side": order.side,
            "lane": order.lane,
            "strategy_id": order.strategy_id,
            "reason": reason,
        }

    @staticmethod
    def _close_trade(
        pos: Position, exit_date: pd.Timestamp, exit_price: float, reason: str
    ) -> Trade:
        entry_price = pos.entry_price
        pnl = (exit_price - entry_price) * pos.shares
        pnl_pct = (
            (exit_price - entry_price) / entry_price if entry_price > 0 else 0.0
        )
        exit_ts = pd.Timestamp(exit_date)
        holding_days = max((exit_ts - pos.entry_date).days, 0)
        return Trade(
            ticker=pos.ticker,
            entry_date=pos.entry_date.to_pydatetime(),
            entry_price=round(entry_price, 4),
            exit_date=exit_ts.to_pydatetime(),
            exit_price=round(exit_price, 4),
            direction=SignalDirection.BUY,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 6),
            holding_days=holding_days,
            exit_reason=reason,
            strategy_id=pos.strategy_id,
            shares=pos.shares,
            stop_price=round(pos.stop_price, 4),
        )

    @staticmethod
    def _build_equity_curve(
        dates: pd.DatetimeIndex, daily_rows: list[dict], capital: float
    ) -> pd.Series:
        index = [dates[0] - pd.Timedelta(days=1)] + [pd.Timestamp(d) for d in dates]
        values = [capital] + [row["equity"] for row in daily_rows]
        return pd.Series(values, index=pd.DatetimeIndex(index), dtype=float)

    @staticmethod
    def _compute_metrics(
        trades: list[Trade],
        equity_curve: pd.Series,
        daily: pd.DataFrame,
        spy: pd.DataFrame,
        dates: pd.DatetimeIndex,
        cfg: PortfolioConfig,
        ratchet_events: list[dict],
    ) -> dict:
        metrics = compute_aggregate_metrics(trades, equity_curve)

        start_value = float(equity_curve.iloc[0])
        end_value = float(equity_curve.iloc[-1])
        total_return = end_value / start_value - 1.0 if start_value > 0 else 0.0

        rolling_12m = equity_curve / equity_curve.shift(252) - 1.0
        worst_12m = rolling_12m.min()
        if worst_12m is None or pd.isna(worst_12m):
            worst_12m = None

        per_year: dict[str, float] = {}
        previous = start_value
        for year in sorted(equity_curve.index.year.unique()):
            year_end = float(equity_curve[equity_curve.index.year == year].iloc[-1])
            per_year[str(year)] = round(year_end / previous - 1.0, 4) if previous else 0.0
            previous = year_end

        exposures = daily["exposure"]
        breaker_pct = float(daily["breaker"].mean()) if len(daily) else 0.0

        spy_window = spy["Close"][
            (spy.index >= dates[0]) & (spy.index <= dates[-1])
        ].dropna()
        benchmark: dict = {}
        if len(spy_window) >= 2:
            spy_start_date = spy_window.index[0]
            spy_end_date = spy_window.index[-1]
            spy_start = float(spy_window.iloc[0])
            spy_end = float(spy_window.iloc[-1])
            spy_total = spy_end / spy_start - 1.0 if spy_start > 0 else 0.0
            spy_years = (spy_end_date - spy_start_date).days / 365.25
            spy_cagr = (
                (spy_end / spy_start) ** (1.0 / spy_years) - 1.0
                if spy_start > 0 and spy_years > 0
                else 0.0
            )
            spy_dd = float(((spy_window / spy_window.cummax()) - 1.0).min())
            # Compare like for like: the system's return is measured over the
            # same window SPY actually trades, so pre-1993 idle years don't
            # dilute the system and the two CAGRs share one time base.
            sys_start = float(equity_curve.asof(spy_start_date))
            sys_end = float(equity_curve.asof(spy_end_date))
            sys_total = sys_end / sys_start - 1.0 if sys_start > 0 else 0.0
            sys_cagr = (
                (sys_end / sys_start) ** (1.0 / spy_years) - 1.0
                if sys_start > 0 and spy_years > 0
                else 0.0
            )
            benchmark = {
                "window_start": str(spy_start_date.date()),
                "window_end": str(spy_end_date.date()),
                "window_years": round(float(spy_years), 2),
                "spy_total_return": round(spy_total, 4),
                "spy_cagr": round(spy_cagr, 4),
                "spy_max_drawdown": round(spy_dd, 4),
                "system_total_return": round(sys_total, 4),
                "system_cagr": round(sys_cagr, 4),
                "excess_total_return": round(sys_total - spy_total, 4),
                "excess_cagr": round(sys_cagr - spy_cagr, 4),
            }

        metrics.update(
            {
                "equity_start": round(start_value, 2),
                "equity_end": round(end_value, 2),
                "total_return_pct": round(total_return, 6),
                "worst_12m": None if worst_12m is None else round(float(worst_12m), 4),
                "avg_exposure": round(float(exposures.mean()), 4) if len(daily) else 0.0,
                "max_exposure": round(float(exposures.max()), 4) if len(daily) else 0.0,
                "band_violations": int(
                    (daily["exposure"] > cfg.band_max + 1e-6).sum()
                )
                if len(daily)
                else 0,
                "breaker_days_pct": round(breaker_pct, 4),
                "trades_total": len(trades),
                "core_trades": sum(1 for t in trades if t.strategy_id == CORE_LANE),
                "banked_total": round(
                    sum(e["bank"] for e in ratchet_events), 2
                ),
                "principal_end": round(
                    float(daily["principal_target"].iloc[-1]), 2
                )
                if len(daily)
                else round(cfg.principal_start, 2),
                "per_year": per_year,
                "benchmark": benchmark,
            }
        )
        return metrics
