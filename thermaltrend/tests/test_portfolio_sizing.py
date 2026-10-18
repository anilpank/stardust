"""Tests for thermaltrend.portfolio.sizing."""

import math

import pytest

from thermaltrend.portfolio import sizing


def _size(**overrides):
    base = dict(
        equity=10_000.0,
        cash=10_000.0,
        invested=0.0,
        risk_per_trade=0.01,
        stop_distance=4.0,
        entry_price=100.0,
        max_position_pct=0.20,
        min_position_notional=1.0,
        band_max=0.65,
        principal_target=5_000.0,
        positions_used=0,
        max_positions=5,
    )
    base.update(overrides)
    return sizing.size_satellite_entry(**base)


class TestHappyPath:
    def test_risk_budget_determines_shares(self):
        decision = _size(stop_distance=10.0, cash=100_000.0, max_position_pct=1.0,
                         band_max=1.0, principal_target=0.0)
        assert decision.ok
        assert decision.shares == 10
        assert decision.stop_price == pytest.approx(90.0)
        assert decision.notional == pytest.approx(1_000.0)
        assert decision.risk_amount == pytest.approx(100.0)

    def test_whole_shares_truncated(self):
        decision = _size(stop_distance=3.0, max_position_pct=1.0, band_max=1.0,
                         principal_target=0.0, cash=100_000.0,
                         min_position_notional=0.0)
        assert decision.ok
        assert decision.shares == int(100.0 / 3.0)
        assert decision.risk_amount <= 100.0

    def test_reason_ok(self):
        assert _size().reason == sizing.REASON_OK
        assert _size().ok


class TestRejections:
    def test_slots_full(self):
        decision = _size(positions_used=5, max_positions=5)
        assert not decision.ok
        assert decision.reason == sizing.REASON_SLOTS_FULL
        assert decision.shares == 0

    def test_bad_entry_price(self):
        assert _size(entry_price=0.0).reason == sizing.REASON_BAD_PRICE
        assert _size(entry_price=float("nan")).reason == sizing.REASON_BAD_PRICE

    def test_no_stop(self):
        assert _size(stop_distance=0.0).reason == sizing.REASON_NO_STOP
        assert _size(stop_distance=float("inf")).reason == sizing.REASON_NO_STOP

    def test_no_risk_budget(self):
        assert _size(risk_per_trade=0.0).reason == sizing.REASON_NO_RISK_BUDGET
        assert _size(equity=0.0).reason == sizing.REASON_NO_RISK_BUDGET

    def test_risk_too_small_for_one_share(self):
        decision = _size(stop_distance=100.0, risk_per_trade=0.001)
        assert decision.reason == sizing.REASON_RISK_TOO_SMALL

    def test_below_min_notional(self):
        decision = _size(min_position_notional=5_000.0, max_position_pct=1.0,
                         band_max=1.0, principal_target=0.0, cash=100_000.0)
        assert decision.reason == sizing.REASON_BELOW_MIN

    def test_band_full(self):
        decision = _size(band_max=0.005)
        assert decision.reason == sizing.REASON_BAND_FULL

    def test_principal_floor(self):
        decision = _size(principal_target=9_999.0)
        assert decision.reason == sizing.REASON_PRINCIPAL_FLOOR

    def test_position_cap_too_small(self):
        decision = _size(max_position_pct=0.005, entry_price=100.0)
        assert decision.reason == sizing.REASON_CAP_TOO_SMALL


class TestCapsShrinkShares:
    def test_max_position_pct_cap(self):
        decision = _size(stop_distance=4.0, risk_per_trade=0.05,
                         max_position_pct=0.10, band_max=1.0,
                         principal_target=0.0, cash=100_000.0)
        assert decision.ok
        assert decision.shares == 10
        assert decision.notional <= 10_000.0 * 0.10 + 1e-9

    def test_band_headroom_shrinks(self):
        decision = _size(invested=6_000.0, band_max=0.65,
                         max_position_pct=1.0, principal_target=0.0,
                         cash=100_000.0, stop_distance=10.0)
        assert decision.ok
        assert decision.shares == 5
        assert decision.notional == pytest.approx(500.0)

    def test_cash_headroom_shrinks(self):
        decision = _size(cash=5_500.0, principal_target=5_000.0,
                         max_position_pct=1.0, band_max=1.0)
        assert decision.ok
        assert decision.shares == 5

    def test_never_exceeds_any_constraint(self):
        decision = _size(equity=10_000.0, cash=5_100.0, invested=6_400.0,
                         principal_target=5_000.0, band_max=0.65,
                         max_position_pct=0.20, stop_distance=2.0)
        assert decision.ok or not decision.ok
        if decision.ok:
            assert decision.shares * decision.entry_price <= 10_000 * 0.20 + 1e-9
            assert decision.shares * decision.entry_price <= 10_000 * 0.65 - 6_400 + 1e-9
            assert decision.shares * decision.entry_price <= 100 + 1e-9
            assert decision.stop_price > 0
            assert decision.risk_amount <= 10_000 * 0.01 + 1e-9

    def test_stop_positive_when_ok(self):
        decision = _size()
        assert decision.stop_price > 0
        assert decision.shares > 0
        assert math.isfinite(decision.notional)
