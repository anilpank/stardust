"""Configuration for the portfolio system.

All risk parameters live here so the live configuration can be committed as a
JSON file, validated on load, and frozen once the validation gate passes.
"""

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class PortfolioConfig:
    capital: float = 10_000.0
    risk_per_trade: float = 0.0075
    band_min: float = 0.25
    band_max: float = 0.65
    max_positions: int = 5
    max_position_pct: float = 0.20
    min_position_notional: float = 250.0
    stop_atr_multiple: float = 2.0
    atr_lookback: int = 14
    slippage_bps: float = 5.0
    cash_yield: float = 0.0
    principal_start_pct: float = 0.50
    ratchet_rate: float = 0.5
    breaker_pct: float = 0.05
    breaker_reopen_offset: float = 0.01
    breaker_cooldown_days: int = 21
    core_ticker: str = "SPY"
    core_enabled: bool = True
    core_target_weight: float = 0.40
    core_momentum_lookback: int = 126
    regime_lookback_days: int = 252
    regime_bull_threshold: float = 0.10
    regime_bear_threshold: float = -0.10

    def __post_init__(self):
        if self.capital <= 0:
            raise ValueError("capital must be > 0")
        if not 0.0 <= self.risk_per_trade <= 0.10:
            raise ValueError("risk_per_trade must be in [0, 0.10]")
        if not 0.0 <= self.band_min <= self.band_max <= 1.0:
            raise ValueError("band_min/band_max must satisfy 0 <= min <= max <= 1")
        if self.max_positions < 1:
            raise ValueError("max_positions must be >= 1")
        if not 0.0 < self.max_position_pct <= 1.0:
            raise ValueError("max_position_pct must be in (0, 1]")
        if self.min_position_notional < 0:
            raise ValueError("min_position_notional must be >= 0")
        if self.stop_atr_multiple <= 0:
            raise ValueError("stop_atr_multiple must be > 0 (stops are mandatory)")
        if self.slippage_bps < 0:
            raise ValueError("slippage_bps must be >= 0")
        if self.cash_yield < 0:
            raise ValueError("cash_yield must be >= 0")
        if not 0.0 <= self.principal_start_pct <= 1.0:
            raise ValueError("principal_start_pct must be in [0, 1]")
        if not 0.0 <= self.ratchet_rate <= 1.0:
            raise ValueError("ratchet_rate must be in [0, 1]")
        if not 0.0 < self.breaker_pct < 1.0:
            raise ValueError("breaker_pct must be in (0, 1)")
        if not 0.0 <= self.breaker_reopen_offset < self.breaker_pct:
            raise ValueError("breaker_reopen_offset must be in [0, breaker_pct)")
        if self.breaker_cooldown_days < 1:
            raise ValueError("breaker_cooldown_days must be >= 1")
        if not 0.0 <= self.core_target_weight <= 1.0:
            raise ValueError("core_target_weight must be in [0, 1]")
        if self.core_momentum_lookback < 1:
            raise ValueError("core_momentum_lookback must be >= 1")
        if self.regime_bear_threshold >= self.regime_bull_threshold:
            raise ValueError("regime_bear_threshold must be < regime_bull_threshold")

    @property
    def principal_start(self) -> float:
        return self.capital * self.principal_start_pct

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PortfolioConfig":
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown))}")
        return cls(**data)


def load_config(path: str | Path | None = None) -> PortfolioConfig:
    if path is None:
        return PortfolioConfig()
    return PortfolioConfig.from_dict(json.loads(Path(path).read_text()))


def save_config(config: PortfolioConfig, path: str | Path) -> None:
    Path(path).write_text(json.dumps(config.to_dict(), indent=2) + "\n")
