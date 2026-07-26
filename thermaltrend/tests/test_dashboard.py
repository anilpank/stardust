"""Tests for thermaltrend/dashboard.py — dashboard helpers and data flow.

Streamlit widget rendering is not unit-testable (requires running server).
These tests cover the pure-logic helpers and integration with the analytics layer.
"""

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import pytest

from thermaltrend.analytics.compare import run_strategy_analysis
from thermaltrend.analytics.metrics import (
    compute_aggregate_metrics,
    compute_confidence,
    compute_equity_curve,
    compute_per_ticker_metrics,
    compute_period_metrics,
)
from thermaltrend.analytics.regime import classify_regime, compute_regime_metrics
from thermaltrend.charts import equity_curve as chart_equity_curve
from thermaltrend.core.engine import DataEngine
from thermaltrend.core.strategy import (
    ATRTrailingStopStrategy,
    DonchianBreakoutStrategy,
    MACrossoverStrategy,
    RSIMeanReversionStrategy,
)
from thermaltrend.feed import DataFeed

DEFAULT_DATA_DIR = str(Path(__file__).resolve().parent.parent / "data" / "equities")


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


class TestTrafficLight:
    def _import_func(self):
        from thermaltrend.dashboard import _traffic_light
        return _traffic_light

    def test_good_normal(self):
        tl = self._import_func()
        assert tl(80, good=60, warn=40) == "good"

    def test_neutral_normal(self):
        tl = self._import_func()
        assert tl(50, good=60, warn=40) == "neutral"

    def test_bad_normal(self):
        tl = self._import_func()
        assert tl(30, good=60, warn=40) == "bad"

    def test_good_inverted(self):
        tl = self._import_func()
        assert tl(20, good=30, warn=50, invert=True) == "good"

    def test_neutral_inverted(self):
        tl = self._import_func()
        assert tl(40, good=30, warn=50, invert=True) == "neutral"

    def test_bad_inverted(self):
        tl = self._import_func()
        assert tl(60, good=30, warn=50, invert=True) == "bad"

    def test_boundary_good_normal(self):
        tl = self._import_func()
        assert tl(60, good=60, warn=40) == "good"

    def test_boundary_neutral_normal(self):
        tl = self._import_func()
        assert tl(40, good=60, warn=40) == "neutral"


class TestDashboardConstants:
    def test_strategy_registry_keys(self):
        from thermaltrend.dashboard import STRATEGY_REGISTRY
        expected = {"MA 50/200", "Donchian 20/10", "RSI 14", "ATR Trail 20/14/3"}
        assert set(STRATEGY_REGISTRY.keys()) == expected

    def test_strategy_defaults_match_registry(self):
        from thermaltrend.dashboard import STRATEGY_DEFAULTS, STRATEGY_REGISTRY
        for name in STRATEGY_REGISTRY:
            assert name in STRATEGY_DEFAULTS, f"Missing defaults for {name}"

    def test_strategy_descriptions_match_registry(self):
        from thermaltrend.dashboard import STRATEGY_DESCRIPTIONS, STRATEGY_REGISTRY
        for name in STRATEGY_REGISTRY:
            assert name in STRATEGY_DESCRIPTIONS, f"Missing description for {name}"

    def test_all_tickers_loads(self):
        from thermaltrend.dashboard import ALL_TICKERS
        assert len(ALL_TICKERS) > 0
        assert "SPY" not in ALL_TICKERS
        assert isinstance(ALL_TICKERS, list)
        assert ALL_TICKERS == sorted(ALL_TICKERS)


class TestBacktestIntegration:
    """End-to-end test: run a backtest and verify the full data flow
    that the dashboard relies on."""

    def test_donchian_produces_full_result(self, tmp_path):
        dates = pd.bdate_range("2026-01-01", periods=60)
        closes = [100 + i * (0.5 if i % 8 < 4 else -0.3) for i in range(60)]
        _make_parquet(tmp_path, "TEST", list(zip(dates, closes)))

        feed = DataFeed(str(tmp_path), tickers=["TEST"], start_date="2026-01-01")
        strategy = DonchianBreakoutStrategy(entry_period=10, exit_period=5)
        engine = DataEngine(feed, strategy)
        signals = engine.run()

        result = run_strategy_analysis(signals, feed._data, "Donchian 20/10")

        assert "metrics" in result
        assert "trades" in result
        assert "equity_curve" in result
        assert "per_ticker" in result
        assert "confidence" in result

        m = result["metrics"]
        assert "cagr" in m
        assert "sharpe" in m
        assert "max_drawdown" in m
        assert "win_rate" in m
        assert "total_trades" in m

        assert isinstance(result["equity_curve"], pd.Series)
        assert len(result["equity_curve"]) > 0

        assert isinstance(result["confidence"], float)
        assert 0.0 <= result["confidence"] <= 1.0

    def test_equity_curve_chartable(self, tmp_path):
        dates = pd.bdate_range("2026-01-01", periods=60)
        closes = [100 + i * (0.5 if i % 8 < 4 else -0.3) for i in range(60)]
        _make_parquet(tmp_path, "TEST", list(zip(dates, closes)))

        feed = DataFeed(str(tmp_path), tickers=["TEST"], start_date="2026-01-01")
        strategy = DonchianBreakoutStrategy(entry_period=10, exit_period=5)
        engine = DataEngine(feed, strategy)
        signals = engine.run()
        result = run_strategy_analysis(signals, feed._data, "Donchian")

        equity = result["equity_curve"]
        fig = chart_equity_curve(equity)
        assert isinstance(fig, go.Figure)

    def test_per_ticker_metrics_structure(self, tmp_path):
        dates = pd.bdate_range("2026-01-01", periods=60)
        closes_a = [100 + i * 0.5 for i in range(60)]
        closes_b = [200 + i * 0.3 for i in range(60)]
        _make_parquet(tmp_path, "A", list(zip(dates, closes_a)))
        _make_parquet(tmp_path, "B", list(zip(dates, closes_b)))

        feed = DataFeed(str(tmp_path), tickers=["A", "B"], start_date="2026-01-01")
        strategy = DonchianBreakoutStrategy(entry_period=10, exit_period=5)
        engine = DataEngine(feed, strategy)
        signals = engine.run()
        result = run_strategy_analysis(signals, feed._data, "Donchian")

        per_ticker = result["per_ticker"]
        assert isinstance(per_ticker, dict)

        for ticker, metrics in per_ticker.items():
            assert isinstance(metrics, dict)
            assert "total_trades" in metrics

    def test_regime_analysis_integration(self, tmp_path):
        dates = pd.bdate_range("2026-01-01", periods=60)
        closes = [100 + i * (0.5 if i % 8 < 4 else -0.3) for i in range(60)]
        _make_parquet(tmp_path, "TEST", list(zip(dates, closes)))

        feed = DataFeed(str(tmp_path), tickers=["TEST"], start_date="2026-01-01")
        strategy = DonchianBreakoutStrategy(entry_period=10, exit_period=5)
        engine = DataEngine(feed, strategy)
        signals = engine.run()
        result = run_strategy_analysis(signals, feed._data, "Donchian")

        spy_path = Path(__file__).resolve().parent.parent / "data" / "equities" / "SPY.parquet"
        if spy_path.exists():
            spy = pd.read_parquet(spy_path)
            regimes = classify_regime(spy["Close"])
            regime_m = compute_regime_metrics(result["trades"], regimes)
            assert isinstance(regime_m, dict)
            for regime_name in ["bull", "bear", "sideways"]:
                assert regime_name in regime_m
                assert "total_trades" in regime_m[regime_name]

    def test_all_strategies_produce_valid_results(self, tmp_path):
        dates = pd.bdate_range("2026-01-01", periods=80)
        closes = [100 + i * (0.8 if i % 10 < 5 else -0.5) for i in range(80)]
        _make_parquet(tmp_path, "TEST", list(zip(dates, closes)))

        strategies = {
            "MA": (MACrossoverStrategy, {"fast_period": 5, "slow_period": 15}),
            "Donchian": (DonchianBreakoutStrategy, {"entry_period": 10, "exit_period": 5}),
            "RSI": (RSIMeanReversionStrategy, {"period": 5, "oversold": 30, "overbought": 70}),
            "ATR": (ATRTrailingStopStrategy, {"entry_period": 10, "atr_period": 5, "atr_multiple": 2.0}),
        }

        for name, (cls, params) in strategies.items():
            feed = DataFeed(str(tmp_path), tickers=["TEST"], start_date="2026-01-01")
            strategy = cls(**params)
            engine = DataEngine(feed, strategy)
            signals = engine.run()
            result = run_strategy_analysis(signals, feed._data, name)

            m = result["metrics"]
            assert isinstance(m["cagr"], float), f"{name}: cagr not float"
            assert isinstance(m["sharpe"], float), f"{name}: sharpe not float"
            assert isinstance(m["total_trades"], int), f"{name}: total_trades not int"
            assert 0.0 <= result["confidence"] <= 1.0, f"{name}: confidence out of range"
