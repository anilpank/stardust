"""Tests for thermaltrend.portfolio.risk."""

import pytest

from thermaltrend.portfolio.config import PortfolioConfig
from thermaltrend.portfolio.risk import (
    Breaker,
    bank_amount,
    entries_allowed,
    exposure_ok,
    principal_floor_ok,
)


@pytest.fixture
def cfg():
    return PortfolioConfig(
        capital=10_000.0,
        breaker_pct=0.05,
        breaker_reopen_offset=0.01,
    )


class TestExposureAndFloor:
    def test_exposure_at_band_is_ok(self):
        assert exposure_ok(6_500.0, 10_000.0, 0.65)

    def test_exposure_over_band_fails(self):
        assert not exposure_ok(6_501.0, 10_000.0, 0.65)

    def test_zero_equity_fails(self):
        assert not exposure_ok(0.0, 0.0, 0.65)

    def test_principal_floor_boundaries(self):
        assert principal_floor_ok(5_000.0, 5_000.0)
        assert not principal_floor_ok(4_999.0, 5_000.0)


class TestBankAmount:
    def test_no_bank_below_last_hwm(self):
        assert bank_amount(9_500.0, 10_000.0, 0.5) == 0.0

    def test_no_bank_at_exact_level(self):
        assert bank_amount(10_000.0, 10_000.0, 0.5) == 0.0

    def test_half_of_gains(self):
        assert bank_amount(11_000.0, 10_000.0, 0.5) == pytest.approx(500.0)

    def test_zero_rate(self):
        assert bank_amount(11_000.0, 10_000.0, 0.0) == 0.0


class TestEntriesAllowed:
    def test_breaker_blocks(self):
        assert not entries_allowed(True, None)
        assert not entries_allowed(True, "bull")

    def test_bear_blocks(self):
        assert not entries_allowed(False, "bear")

    def test_others_allow(self):
        assert entries_allowed(False, None)
        assert entries_allowed(False, "sideways")
        assert entries_allowed(False, "bull")


class TestBreaker:
    def test_starts_open(self, cfg):
        assert not Breaker().tripped

    def test_trips_at_threshold(self, cfg):
        breaker = Breaker()
        assert breaker.update(9_500.0, 10_000.0, cfg) is True
        assert breaker.tripped

    def test_trips_below_threshold(self, cfg):
        breaker = Breaker()
        breaker.update(9_400.0, 10_000.0, cfg)
        assert breaker.tripped

    def test_no_trip_above_threshold(self, cfg):
        breaker = Breaker()
        assert breaker.update(9_501.0, 10_000.0, cfg) is False
        assert not breaker.tripped

    def test_stays_tripped_between_trip_and_reopen(self, cfg):
        breaker = Breaker()
        breaker.update(9_400.0, 10_000.0, cfg)
        assert breaker.update(9_550.0, 10_000.0, cfg) is False
        assert breaker.tripped

    def test_reopens_at_recovery_buffer(self, cfg):
        breaker = Breaker()
        breaker.update(9_400.0, 10_000.0, cfg)
        assert breaker.update(9_600.0, 10_000.0, cfg) is True
        assert not breaker.tripped

    def test_hysteresis_gap(self, cfg):
        breaker = Breaker()
        breaker.update(9_400.0, 10_000.0, cfg)
        assert breaker.update(9_599.0, 10_000.0, cfg) is False
        assert breaker.tripped

    def test_retrip_after_reopen(self, cfg):
        breaker = Breaker()
        breaker.update(9_400.0, 10_000.0, cfg)
        breaker.update(9_700.0, 10_000.0, cfg)
        assert not breaker.tripped
        assert breaker.update(9_500.0, 10_000.0, cfg) is True
        assert breaker.tripped

    def test_growing_hwm_scales_threshold(self, cfg):
        breaker = Breaker()
        breaker.update(11_000.0, 12_000.0, cfg)
        assert breaker.tripped

    def test_cooldown_reopens_flat_cash_account(self, cfg):
        breaker = Breaker()
        breaker.update(9_000.0, 10_000.0, cfg)
        assert breaker.tripped
        for _ in range(cfg.breaker_cooldown_days - 2):
            assert breaker.update(9_000.0, 10_000.0, cfg) is False
            assert breaker.tripped
        reopened = breaker.update(9_000.0, 10_000.0, cfg)
        assert reopened is True
        assert not breaker.tripped

    def test_cooldown_resets_after_retrip(self, cfg):
        breaker = Breaker()
        breaker.update(9_400.0, 10_000.0, cfg)
        for _ in range(cfg.breaker_cooldown_days - 2):
            breaker.update(9_400.0, 10_000.0, cfg)
        assert breaker.tripped
        assert breaker.update(9_000.0, 10_000.0, cfg) is True
        assert not breaker.tripped
        assert breaker.update(9_400.0, 10_000.0, cfg) is True
        assert breaker.tripped
        assert breaker.days_tripped == 1
