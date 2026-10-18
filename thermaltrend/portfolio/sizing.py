"""Fixed-fractional risk sizing for satellite positions.

Risk per trade is a fraction of total portfolio equity measured as the
distance from entry to the ATR stop. Shares are whole; notional, exposure
band, principal floor, slot count, and a minimum-notional floor are enforced
as hard constraints. Every rejection returns a machine-readable reason so the
daily brief can explain why an order was not taken.
"""

import math
from dataclasses import dataclass

REASON_OK = "ok"
REASON_SLOTS_FULL = "slots_full"
REASON_BAD_PRICE = "bad_price"
REASON_NO_STOP = "no_stop"
REASON_NO_RISK_BUDGET = "no_risk_budget"
REASON_RISK_TOO_SMALL = "risk_too_small"
REASON_BAND_FULL = "band_full"
REASON_PRINCIPAL_FLOOR = "principal_floor"
REASON_BELOW_MIN = "below_min_notional"
REASON_CAP_TOO_SMALL = "position_cap_too_small"


@dataclass(frozen=True)
class SizingDecision:
    shares: int
    entry_price: float
    stop_price: float
    notional: float
    risk_amount: float
    reason: str

    @property
    def ok(self) -> bool:
        return self.reason == REASON_OK and self.shares > 0


def _reject(reason: str) -> SizingDecision:
    return SizingDecision(0, 0.0, 0.0, 0.0, 0.0, reason)


def size_satellite_entry(
    *,
    equity: float,
    cash: float,
    invested: float,
    risk_per_trade: float,
    stop_distance: float,
    entry_price: float,
    max_position_pct: float,
    min_position_notional: float,
    band_max: float,
    principal_target: float,
    positions_used: int,
    max_positions: int,
) -> SizingDecision:
    """Size one satellite entry at fill time.

    All monetary inputs are evaluated against the same reference valuations
    (equity/invested from the previous close, cash current after any morning
    sells). Returns a SizingDecision whose ``reason`` is ``"ok"`` only when
    every constraint holds for at least one whole share.
    """
    if positions_used >= max_positions:
        return _reject(REASON_SLOTS_FULL)
    if not math.isfinite(entry_price) or entry_price <= 0:
        return _reject(REASON_BAD_PRICE)
    if not math.isfinite(stop_distance) or stop_distance <= 0:
        return _reject(REASON_NO_STOP)
    if equity <= 0:
        return _reject(REASON_NO_RISK_BUDGET)

    budget = equity * risk_per_trade
    if budget <= 0:
        return _reject(REASON_NO_RISK_BUDGET)

    shares = int(budget / stop_distance)
    if shares < 1:
        return _reject(REASON_RISK_TOO_SMALL)

    cap_shares = int((equity * max_position_pct) / entry_price)
    if cap_shares < 1:
        return _reject(REASON_CAP_TOO_SMALL)
    shares = min(shares, cap_shares)

    band_shares = int((equity * band_max - invested) / entry_price)
    if band_shares < 1:
        return _reject(REASON_BAND_FULL)
    shares = min(shares, band_shares)

    cash_shares = int((cash - principal_target) / entry_price)
    if cash_shares < 1:
        return _reject(REASON_PRINCIPAL_FLOOR)
    shares = min(shares, cash_shares)

    if shares < 1:
        return _reject(REASON_RISK_TOO_SMALL)

    notional = shares * entry_price
    if notional < min_position_notional:
        return _reject(REASON_BELOW_MIN)

    stop_price = entry_price - stop_distance
    if stop_price <= 0:
        return _reject(REASON_NO_STOP)

    return SizingDecision(
        shares=shares,
        entry_price=entry_price,
        stop_price=stop_price,
        notional=round(notional, 2),
        risk_amount=round(shares * stop_distance, 2),
        reason=REASON_OK,
    )
