"""Portfolio-level risk rules: exposure band, regime gate, breaker, ratchet.

These are the mechanisms that bound the loss envelope on total capital:
- exposure band: hard cap on invested fraction of equity
- principal floor: cash can never be spent below the principal sleeve target
- regime gate: no new entries while the market regime is BEAR
- breaker: no new entries after equity falls ``breaker_pct`` below its
  high-water mark. Reopens on either leg of a hysteresis pair: equity recovers
  ``breaker_reopen_offset`` above the HWM-relative trip level, or the breaker
  has been tripped for ``breaker_cooldown_days`` trading days. The cooldown is
  a liveness guarantee: with entries blocked and the account in cash, equity
  cannot grow on its own, so a pure level-based reopen would deadlock the
  system forever after a single drawdown.
- ratchet: monthly bank of a fraction of gains above the last banked level
  into the principal sleeve (one-way; principal never gives back)
"""

from dataclasses import dataclass

from thermaltrend.portfolio.config import PortfolioConfig

TOLERANCE = 1e-6
BEAR_REGIME = "bear"


def exposure_ok(invested: float, equity: float, band_max: float) -> bool:
    return equity > 0 and invested <= equity * band_max + TOLERANCE


def principal_floor_ok(cash_after: float, principal_target: float) -> bool:
    return cash_after >= principal_target - TOLERANCE


def bank_amount(equity: float, last_banked_hwm: float, ratchet_rate: float) -> float:
    if equity <= last_banked_hwm:
        return 0.0
    return (equity - last_banked_hwm) * ratchet_rate


def entries_allowed(breaker_tripped: bool, regime_value: str | None) -> bool:
    if breaker_tripped:
        return False
    if regime_value == BEAR_REGIME:
        return False
    return True


@dataclass
class Breaker:
    tripped: bool = False
    days_tripped: int = 0

    def update(self, equity: float, hwm: float, config: PortfolioConfig) -> bool:
        """Advance the breaker state; returns True when the state changed."""
        if not self.tripped:
            self.days_tripped = 0
            trip_level = hwm * (1.0 - config.breaker_pct)
            if equity <= trip_level:
                self.tripped = True
                self.days_tripped = 1
                return True
            return False

        self.days_tripped += 1
        reopen_level = hwm * (
            1.0 - config.breaker_pct + config.breaker_reopen_offset
        )
        cooled = self.days_tripped >= config.breaker_cooldown_days
        if equity >= reopen_level or cooled:
            self.tripped = False
            return True
        return False
