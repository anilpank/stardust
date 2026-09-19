"""Tests for FactorScoringStrategy — multi-factor composite score."""

import math
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from thermaltrend.core.events import MarketEvent, SignalDirection
from thermaltrend.core.strategy import (
    FACTOR_NAMES,
    FactorScoringStrategy,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_parquet(tmp_path, ticker, dates_closes):
    df = pd.DataFrame(
        {"Open": [c for _, c in dates_closes],
         "High": [c + 1 for _, c in dates_closes],
         "Low": [c - 1 for _, c in dates_closes],
         "Close": [c for _, c in dates_closes],
         "Volume": [1000] * len(dates_closes),
         "ticker": ticker},
        index=pd.DatetimeIndex([d for d, _ in dates_closes], name="Date"),
    )
    df.to_parquet(tmp_path / f"{ticker}.parquet")


def _up_down(n=600):
    """Steeply-up since the first regime change, then declining."""
    closes = []
    for i in range(n):
        if i < 250:
            closes.append(100.0 * 1.002 ** i)
        elif i < 350:
            closes.append(100.0 * 1.002 ** 250 * 1.006 ** (i - 250))
        else:
            closes.append(
                100.0 * 1.002 ** 250 * 1.006 ** 100 * 0.9995 ** (i - 350)
            )
    return closes


def _declining_lowvol(n=400, seed=7):
    """Price drifts down while volatility falls monotonically.

    A steadily shrinking volatility makes the normalized low-vol factor sit
    high -- yet momentum stays negative, exposing the absolute-momentum gate.
    """
    rng = np.random.default_rng(seed)
    envelope = np.linspace(1.0, 0.02, n)
    rets = rng.normal(loc=-0.002, scale=0.002, size=n) * envelope
    closes = 100.0 * np.exp(np.cumsum(rets))
    return list(map(float, closes))


def _slow_rise(n=200):
    """Gentle geometric climb -- momentum positive, every factor neutral."""
    return [100.0 * 1.0005 ** i for i in range(n)]


def _default_params():
    return {
        "momentum_lookback": 60,
        "volatility_lookback": 40,
        "trend_fast": 10,
        "trend_slow": 40,
        "reversion_lookback": 10,
        "window": 80,
        "entry_threshold": 0.60,
        "exit_threshold": 0.50,
        "absolute_momentum": True,
    }


def _run(series, params=None, ticker="TEST"):
    """Feed a close series into a fresh strategy instance, return signals."""
    strategy = FactorScoringStrategy(**(params or _default_params()))
    start = datetime(2020, 1, 1)
    events = [
        MarketEvent(
            timestamp=start + timedelta(days=i),
            ticker=ticker,
            open=c,
            high=c + 1,
            low=c - 1,
            close=c,
            volume=1000,
        )
        for i, c in enumerate(series)
    ]
    signals = [s for e in events if (s := strategy.on_market(e)) is not None]
    return signals, strategy


# ---------------------------------------------------------------------------
# factor math
# ---------------------------------------------------------------------------

class TestFactorMath:
    def test_momentum_raw(self):
        s = FactorScoringStrategy(**{**_default_params(), "momentum_lookback": 20})
        closes = list(range(100, 141, 1))  # 41 closes
        assert s._momentum_return(closes[:20]) is None  # too few closes
        raw = s._raw_factor("momentum", closes)
        assert raw is not None
        assert raw == pytest.approx(closes[-1] / closes[-21] - 1)

    def test_trend_sign(self):
        s = FactorScoringStrategy(**{**_default_params(), "trend_fast": 5, "trend_slow": 10})
        rising = [100.0 * 1.01 ** i for i in range(20)]
        falling = [100.0 * 0.99 ** i for i in range(20)]
        assert s._raw_factor("trend", rising) > 0
        assert s._raw_factor("trend", falling) < 0
        assert s._raw_factor("trend", [100.0] * 20) == 0.0

    def test_reversal_is_negative_return(self):
        s = FactorScoringStrategy(**{**_default_params(), "reversion_lookback": 5})
        closes = [100.0, 105.0, 110.0, 115.0, 120.0, 100.0, 100.0]
        ret = closes[-1] / closes[-6] - 1
        assert s._raw_factor("reversal", closes) == pytest.approx(-ret)

    def test_low_vol_never_positive(self):
        s = FactorScoringStrategy()
        closes = [100.0 * (1 + 0.01 * math.sin(i)) for i in range(100)]
        assert s._raw_factor("low_vol", closes) <= 0.0
        # constant closes -> zero variance
        assert s._raw_factor("low_vol", [100.0] * 100) == 0.0

    def test_normalize_bounds_and_std_floor(self):
        f = FactorScoringStrategy._normalize
        for v in (-100.0, -2.0, 0.0, 2.0, 100.0):
            assert 0.0 <= f(v, [0.0, 1.0, -1.0]) <= 1.0
        assert f(0.1, [0.1, 0.1, 0.1]) == 0.5  # degenerate window
        assert f(1.0, [1.0]) == 0.5  # single value
        assert f(1.0, []) == 0.5

    def test_weights_validated(self):
        with pytest.raises(ValueError, match="Unknown factor"):
            FactorScoringStrategy(weights={"bogus": 1.0})
        with pytest.raises(ValueError, match="positive"):
            FactorScoringStrategy(weights={"momentum": 0.0, "low_vol": 0.0,
                                           "trend": 0.0, "reversal": 0.0})
        s = FactorScoringStrategy(weights={"momentum": 2.0, "trend": 2.0})
        assert sum(s.weights.values()) == pytest.approx(1.0)
        assert s.weights["momentum"] == pytest.approx(0.5)
        assert s._active == ["momentum", "trend"]

    def test_composite_within_bounds_after_warmup(self):
        strategy = FactorScoringStrategy(**_default_params())
        start = datetime(2020, 1, 1)
        for i, c in enumerate(_up_down(400)):
            strategy.on_market(_ev(i, c))
        score = strategy._composite("TEST")
        assert score is not None
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# behavior
# ---------------------------------------------------------------------------

class TestBehavior:
    def test_no_signals_before_warmup(self):
        signals, _ = _run(_up_down(100))
        assert signals == []

    def test_no_signals_on_neutral_score(self):
        # Every factor degenerates to 0.5 on a steady climb, leaving the
        # composite below the entry threshold: no signals at all.
        signals, strategy = _run(_slow_rise(300))
        assert signals == []
        assert strategy._prev_score["TEST"] == pytest.approx(0.5)

    def test_buy_on_strong_uptrend(self):
        signals, strategy = _run(_up_down(420))
        buys = [s for s in signals if s.direction == SignalDirection.BUY]
        assert buys, "expected at least one BUY on an uptrend"
        assert buys[0].metadata["score"] > buys[0].metadata["entry_threshold"]
        assert buys[0].metadata["absolute_pass"] is True
        assert 0.0 < buys[0].strength <= 1.0

    def test_sell_after_downturn(self):
        signals, _ = _run(_up_down(500))
        sells = [s for s in signals if s.direction == SignalDirection.SELL]
        assert sells, "expected at least one SELL after the trend turns down"
        assert 0.0 < sells[0].strength <= 1.0

    def test_absolute_momentum_gate_blocks_buy_on_decline(self):
        # Shrinking volatility drives the low-vol factor (weight 1.0) above
        # the entry threshold while the decline keeps raw momentum negative --
        # exactly the situation the gate exists to block.
        closes = _declining_lowvol(400)

        def feed(fresh: FactorScoringStrategy):
            return [
                s for s in (
                    fresh.on_market(_ev(i, c)) for i, c in enumerate(closes)
                )
                if s is not None
            ]

        gated = FactorScoringStrategy(
            **{**_default_params(), "weights": {"low_vol": 1.0}}
        )
        signals_gated = feed(gated)
        assert not any(
            s.direction == SignalDirection.BUY for s in signals_gated
        )

        ungated = FactorScoringStrategy(
            **{**_default_params(), "weights": {"low_vol": 1.0},
               "absolute_momentum": False}
        )
        signals_ungated = feed(ungated)
        buys = [s for s in signals_ungated if s.direction == SignalDirection.BUY]
        assert buys, "expected BUYs once the gate is disabled"
        # At the buy bar the raw momentum return was negative, proving the
        # gate (not score) was what held the gated run back.
        assert all(b.metadata["momentum_return"] < 0 for b in buys)

    def test_hysteresis_between_thresholds(self):
        # A steady climb keeps composite at the neutral 0.5 (between the
        # 0.5 exit and 0.6 entry thresholds): flat stays flat and, once in a
        # position, the position is retained with no signals emitted.
        params = _default_params()
        strategy = FactorScoringStrategy(**params)
        start = datetime(2020, 1, 1)
        for i, c in enumerate(_slow_rise(200)):
            ev = _ev(i, c)
            s = strategy.on_market(ev)
            assert s is None
        score = strategy._composite("TEST")
        assert score is not None and 0.5 <= score < 0.6
        assert strategy._in_position["TEST"] is False

        # Same series but already in a position: no SELL while score stays
        # at/above the exit threshold. The position is injected after the
        # first event so the per-ticker init block doesn't reset it.
        strategy2 = FactorScoringStrategy(**params)
        strategy2.on_market(_ev(0, 100.0))
        strategy2._in_position["TEST"] = True
        for i, c in enumerate(_slow_rise(200)):
            s = strategy2.on_market(_ev(i + 1, c))
            assert s is None
        assert strategy2._in_position["TEST"] is True

    def test_metadata_contents(self):
        signals, _ = _run(_up_down(420))
        meta = signals[0].metadata
        for key in ("score", "entry_threshold", "exit_threshold",
                    "momentum_return", "absolute_momentum",
                    "absolute_pass", "factors", "weights"):
            assert key in meta
        assert set(meta["weights"]) == set(FACTOR_NAMES)


def _ev(i, c):
    return MarketEvent(
        timestamp=datetime(2020, 1, 1) + timedelta(days=i),
        ticker="TEST",
        open=c,
        high=c + 1,
        low=c - 1,
        close=c,
        volume=1000,
    )


# ---------------------------------------------------------------------------
# registry integration
# ---------------------------------------------------------------------------

class TestRegistryIntegration:
    def test_backtest_registry(self, tmp_path):
        dates = pd.bdate_range("2020-01-01", periods=400)
        _make_parquet(tmp_path, "TEST", list(zip(dates, _up_down(400))))
        from thermaltrend.backtest import run_backtest

        result = run_backtest(
            "factor_scoring", ["TEST"], start_date="2020-01-01",
            params=_default_params(), data_dir=str(tmp_path),
        )
        assert result["strategy_name"] == "factor_scoring"

    def test_signals_cli(self, tmp_path):
        dates = pd.bdate_range("2020-01-01", periods=400)
        _make_parquet(tmp_path, "TEST", list(zip(dates, _up_down(400))))
        script = Path(__file__).resolve().parent.parent / "signals.py"
        result = subprocess.run(
            [sys.executable, str(script), "--strategy", "factor_scoring",
             "--tickers", "TEST", "--start", "2020-01-01",
             "--data-dir", str(tmp_path)],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert "factor_scoring" in result.stdout

    def test_compare_includes_factor_scoring(self, tmp_path):
        dates = pd.bdate_range("2020-01-01", periods=400)
        _make_parquet(tmp_path, "TEST", list(zip(dates, _up_down(400))))
        from thermaltrend.compare_cli import run_compare

        ranking, _ = run_compare(
            tickers=["TEST"], strategy_names=["factor_scoring"],
            start_date="2020-01-01", data_dir=str(tmp_path),
        )
        assert "Factor 126d" in ranking["strategy"].values

    def test_walk_forward_grid(self, tmp_path):
        dates = pd.bdate_range("2020-01-01", periods=600)
        _make_parquet(tmp_path, "TEST", list(zip(dates, _up_down(600))))
        from thermaltrend.walk_forward import run_walk_forward

        result = run_walk_forward(
            "factor_scoring", ["TEST"], start_date="2020-01-01",
            grid={"momentum_lookback": [40, 60], "window": [60, 80]},
            train_days=100, test_days=50, step_days=50, warmup_days=250,
            data_dir=str(tmp_path),
        )
        assert not result.rows.empty
        for combo in result.params_by_window:
            assert combo["momentum_lookback"] in (40, 60)
            assert combo["window"] in (60, 80)

    def test_dashboard_wiring(self, monkeypatch):
        import thermaltrend.dashboard as dash

        assert "Factor 126d" in dash.STRATEGY_REGISTRY
        assert dash.STRATEGY_REGISTRY["Factor 126d"] is FactorScoringStrategy
        defaults = dash.STRATEGY_DEFAULTS["Factor 126d"]
        assert defaults["entry_threshold"] == 0.60
        assert defaults["exit_threshold"] == 0.50
        assert "Factor 126d" in dash.STRATEGY_DESCRIPTIONS

        # No SPY benchmark injection for factor scoring.
        assert dash.resolve_feed_tickers("Factor 126d", ["AAPL"]) == ["AAPL"]