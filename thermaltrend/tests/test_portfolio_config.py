"""Tests for thermaltrend.portfolio.config."""

import json

import pytest

from thermaltrend.portfolio.config import PortfolioConfig, load_config, save_config


class TestDefaults:
    def test_default_config_valid(self):
        cfg = PortfolioConfig()
        assert cfg.capital == 10_000.0
        assert cfg.band_min <= cfg.band_max
        assert cfg.core_ticker == "SPY"

    def test_principal_start_property(self):
        cfg = PortfolioConfig(capital=20_000.0, principal_start_pct=0.6)
        assert cfg.principal_start == pytest.approx(12_000.0)


class TestValidation:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"capital": 0.0},
            {"capital": -1.0},
            {"risk_per_trade": -0.01},
            {"risk_per_trade": 0.5},
            {"band_min": 0.8, "band_max": 0.5},
            {"band_max": 1.5},
            {"max_positions": 0},
            {"max_position_pct": 0.0},
            {"min_position_notional": -1.0},
            {"stop_atr_multiple": 0.0},
            {"slippage_bps": -1.0},
            {"principal_start_pct": 1.5},
            {"ratchet_rate": 2.0},
            {"breaker_pct": 0.0},
            {"breaker_pct": 1.5},
            {"breaker_reopen_offset": 0.1, "breaker_pct": 0.05},
            {"breaker_cooldown_days": 0},
            {"core_target_weight": 1.5},
            {"core_momentum_lookback": 0},
            {"regime_bear_threshold": 0.2, "regime_bull_threshold": 0.1},
        ],
    )
    def test_invalid_values_raise(self, overrides):
        with pytest.raises(ValueError):
            PortfolioConfig(**overrides)

    def test_valid_extremes(self):
        cfg = PortfolioConfig(
            capital=10_000,
            risk_per_trade=0.0,
            band_min=0.0,
            band_max=1.0,
            breaker_reopen_offset=0.0,
        )
        assert cfg.risk_per_trade == 0.0


class TestSerialization:
    def test_round_trip_dict(self):
        cfg = PortfolioConfig(capital=15_000.0, risk_per_trade=0.01)
        assert PortfolioConfig.from_dict(cfg.to_dict()) == cfg

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError, match="Unknown config keys"):
            PortfolioConfig.from_dict({"capital": 10_000, "typo_key": 1})

    def test_load_default_when_no_path(self):
        assert load_config(None) == PortfolioConfig()

    def test_save_and_load(self, tmp_path):
        path = tmp_path / "cfg.json"
        cfg = PortfolioConfig(capital=25_000.0, band_max=0.5, band_min=0.2)
        save_config(cfg, path)
        assert json.loads(path.read_text())["capital"] == 25_000.0
        assert load_config(path) == cfg
