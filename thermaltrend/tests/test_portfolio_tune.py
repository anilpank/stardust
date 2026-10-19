"""Tests for thermaltrend.portfolio_tune."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from thermaltrend.backtest import DEFAULT_DATA_DIR
from thermaltrend.portfolio.config import load_config, save_config
from thermaltrend.portfolio_tune import (
    DEFAULT_GRID,
    ENVELOPE,
    base_config,
    format_tune_table,
    run_portfolio_tune,
    segment_metrics,
    select_train_config,
)

GRID_KEYS = {"core_target_weight", "core_momentum_lookback", "breaker_pct"}


def _rising_curve(periods: int = 300, start: float = 100.0, rate: float = 0.0005):
    dates = pd.bdate_range("2020-01-02", periods=periods)
    values = start * (1.0 + rate) ** np.arange(periods)
    return pd.Series(values, index=dates)


class TestBaseConfig:
    def test_sets_realistic_slippage_and_yield(self):
        cfg = base_config(slippage_bps=10.0, cash_yield=0.04)
        assert cfg.slippage_bps == 10.0
        assert cfg.cash_yield == 0.04
        assert cfg.core_enabled is True


class TestSegmentMetrics:
    def test_monotonic_rise_has_no_drawdown(self):
        curve = _rising_curve()
        m = segment_metrics(curve, curve.index[0], curve.index[-1])
        assert m["cagr"] > 0
        assert m["max_drawdown"] == 0.0
        assert m["worst_12m"] > 0

    def test_known_decline(self):
        periods = 253
        dates = pd.bdate_range("2020-01-02", periods=periods)
        values = np.linspace(100.0, 90.0, periods)
        curve = pd.Series(values, index=dates)
        m = segment_metrics(curve, dates[0], dates[-1])
        assert m["max_drawdown"] == pytest.approx(-0.10, abs=1e-3)
        assert m["worst_12m"] == pytest.approx(-0.10, abs=1e-3)
        assert m["total_return"] == pytest.approx(-0.10, abs=1e-3)
        assert m["cagr"] < 0
        assert m["calmar"] < 0

    def test_slice_before_after_range(self):
        curve = _rising_curve(periods=100)
        m = segment_metrics(
            curve, curve.index[50], curve.index[99]
        )
        assert m["total_return"] > 0

    def test_short_slice_returns_empty(self):
        curve = _rising_curve(periods=10)
        m = segment_metrics(curve, curve.index[0], curve.index[0])
        assert m["cagr"] == 0.0
        assert m["worst_12m"] is None

    def test_empty_series_returns_empty(self):
        m = segment_metrics(pd.Series(dtype=float), "2020-01-01", "2020-12-31")
        assert m["max_drawdown"] == 0.0
        assert m["worst_12m"] is None


class TestSelectTrainConfig:
    def test_prefers_envelope_meeting_config(self):
        candidates = [
            ({"a": 1}, {"calmar": 2.0, "worst_12m": -0.08}),
            ({"a": 2}, {"calmar": 1.0, "worst_12m": -0.02}),
        ]
        combo, metrics, met = select_train_config(candidates, "calmar", 0.05)
        assert combo == {"a": 2}
        assert met is True

    def test_highest_calmar_among_eligible(self):
        candidates = [
            ({"a": 1}, {"calmar": 1.0, "worst_12m": -0.04}),
            ({"a": 2}, {"calmar": 3.0, "worst_12m": -0.01}),
        ]
        combo, _, met = select_train_config(candidates, "calmar", 0.05)
        assert combo == {"a": 2}
        assert met is True

    def test_falls_back_to_least_bad_when_none_meet(self):
        candidates = [
            ({"a": 1}, {"calmar": 5.0, "worst_12m": -0.20}),
            ({"a": 2}, {"calmar": 1.0, "worst_12m": -0.06}),
        ]
        combo, _, met = select_train_config(candidates, "calmar", 0.05)
        assert combo == {"a": 2}
        assert met is False

    def test_none_worst_12m_not_eligible(self):
        candidates = [
            ({"a": 1}, {"calmar": 9.0, "worst_12m": None}),
            ({"a": 2}, {"calmar": 1.0, "worst_12m": -0.03}),
        ]
        combo, _, met = select_train_config(candidates, "calmar", 0.05)
        assert combo == {"a": 2}
        assert met is True

    def test_empty_candidates_raises(self):
        with pytest.raises(ValueError, match="no candidate"):
            select_train_config([], "calmar", 0.05)


def _make_parquet(tmp_path, ticker, dates, closes):
    df = pd.DataFrame(
        {
            "Open": list(closes),
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": list(closes),
            "Volume": [1000] * len(dates),
            "ticker": ticker,
        },
        index=pd.DatetimeIndex(dates, name="Date"),
    )
    df.to_parquet(tmp_path / f"{ticker}.parquet")


@pytest.fixture
def data_dir(tmp_path):
    dates = pd.bdate_range("2020-01-02", periods=400)
    trend = 400.0 + 0.4 * np.arange(len(dates)) + 15.0 * np.sin(
        np.arange(len(dates)) / 25.0
    )
    _make_parquet(tmp_path, "SPY", dates, [float(c) for c in trend])
    return tmp_path


class TestRunPortfolioTune:
    def test_smoke(self, data_dir):
        grid = {"core_target_weight": [0.3, 0.4], "breaker_pct": [0.05]}
        result = run_portfolio_tune(
            data_dir=str(data_dir),
            grid=grid,
            train_days=120,
            test_days=60,
            step_days=60,
            warmup_days=30,
        )
        assert len(result.grid) == 2
        assert not result.rows.empty
        assert set(result.frozen_config.to_dict()) >= GRID_KEYS
        assert result.frozen_config.core_target_weight in (0.3, 0.4)
        assert isinstance(result.envelope_met, bool)
        assert result.envelope == ENVELOPE
        assert len(result.oos_equity_curve) >= 2
        assert "worst_12m" in result.frozen_metrics
        assert result.frozen_metrics["cagr"] is not None

    def test_insufficient_data_raises(self, data_dir):
        with pytest.raises(ValueError, match="Not enough data"):
            run_portfolio_tune(
                data_dir=str(data_dir),
                train_days=500,
                test_days=200,
                step_days=200,
            )

    def test_frozen_config_round_trips(self, data_dir, tmp_path):
        result = run_portfolio_tune(
            data_dir=str(data_dir),
            grid={"core_target_weight": [0.3]},
            train_days=120,
            test_days=60,
            step_days=60,
            warmup_days=30,
        )
        out = tmp_path / "live_config.json"
        save_config(result.frozen_config, out)
        reloaded = load_config(out)
        assert reloaded.to_dict() == result.frozen_config.to_dict()

    def test_default_grid_has_expected_knobs(self):
        assert set(DEFAULT_GRID) == GRID_KEYS


@pytest.mark.skipif(
    not (Path(DEFAULT_DATA_DIR) / "SPY.parquet").exists(),
    reason="committed SPY price data not present",
)
class TestRealDataGate:
    def test_gate_passes_on_committed_data(self):
        result = run_portfolio_tune(
            grid={"core_target_weight": [0.3], "breaker_pct": [0.05]}
        )
        assert result.envelope_met
        assert result.frozen_config.core_target_weight == 0.3


class TestFormatTuneTable:
    def test_prints_report(self, data_dir):
        result = run_portfolio_tune(
            data_dir=str(data_dir),
            grid={"core_target_weight": [0.3]},
            train_days=120,
            test_days=60,
            step_days=60,
            warmup_days=30,
        )
        text = format_tune_table(result)
        assert "Phase A tuning gate" in text
        assert "Held-out OOS" in text
        assert "Frozen config" in text
        assert "Envelope" in text
