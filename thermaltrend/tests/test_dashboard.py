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
        expected = {"MA 50/200", "Donchian 20/10", "RSI 14", "ATR Trail 20/14/3", "Dual Mom 126d", "Factor 126d"}
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


class TestTickerLabels:
    """The pickers show \"TICKER — Company Name\" labels so Streamlit's
    native type-ahead autocomplete doubles as company-name search."""

    def test_label_includes_company_name(self):
        from thermaltrend.dashboard import _ticker_label
        assert _ticker_label("AAPL") == "AAPL — Apple Inc."

    def test_label_includes_brand_alias(self):
        from thermaltrend.dashboard import _ticker_label
        assert "Facebook" in _ticker_label("META")

    def test_options_cover_all_tickers(self):
        from thermaltrend.dashboard import ALL_TICKERS, _ticker_options
        assert len(_ticker_options()) == len(ALL_TICKERS)

    def test_options_unique(self):
        from thermaltrend.dashboard import _ticker_options
        options = _ticker_options()
        assert len(options) == len(set(options))

    def test_spy_not_in_options(self):
        from thermaltrend.dashboard import _ticker_options
        assert all(not label.startswith("SPY — ") for label in _ticker_options())

    def test_defaults_use_labels(self):
        from thermaltrend.dashboard import _ticker_options
        assert _ticker_options(["AAPL", "MSFT", "GOOGL"]) == [
            "AAPL — Apple Inc.",
            "MSFT — Microsoft",
            "GOOGL — Alphabet Inc. (Class A) (Google)",
        ]

    def test_empty_list_is_honored(self):
        from thermaltrend.dashboard import _ticker_options
        assert _ticker_options([]) == []

    def test_options_follow_requested_order(self):
        from thermaltrend.dashboard import _ticker_options
        assert _ticker_options(["F", "A"])[0] == "F — Ford Motor Company"

    def test_parse_reformats_to_same_label(self):
        from thermaltrend.dashboard import _ticker_label, _ticker_options
        from thermaltrend.ticker_search import parse_ticker_from_label
        for label in _ticker_options():
            ticker = parse_ticker_from_label(label)
            assert _ticker_label(ticker) == label

    def test_all_option_labels_roundtrip_to_all_tickers(self):
        from thermaltrend.dashboard import ALL_TICKERS, _ticker_options
        from thermaltrend.ticker_search import parse_ticker_from_label
        parsed = [parse_ticker_from_label(label) for label in _ticker_options()]
        assert parsed == ALL_TICKERS

    def test_selected_tickers_roundtrip(self):
        from thermaltrend.dashboard import _selected_tickers
        labels = ["AAPL — Apple Inc.", "BRK-B — Berkshire Hathaway"]
        assert _selected_tickers(labels) == ["AAPL", "BRK-B"]

    def test_selected_tickers_parses_brand_alias_label(self):
        from thermaltrend.dashboard import _selected_tickers
        assert _selected_tickers(["META — Meta Platforms (Facebook)"]) == ["META"]


class TestResolveFeedTickers:
    """Dual Momentum needs its benchmark bars in the feed even though it
    never trades them. The dashboard ticker picker excludes SPY by design,
    so resolve_feed_tickers injects it."""

    def test_other_strategies_unchanged(self):
        from thermaltrend.dashboard import resolve_feed_tickers
        for name in ["MA 50/200", "Donchian 20/10", "RSI 14", "ATR Trail 20/14/3"]:
            assert resolve_feed_tickers(name, ["AAPL", "MSFT"]) == ["AAPL", "MSFT"]

    def test_dual_momentum_appends_spy(self):
        from thermaltrend.dashboard import resolve_feed_tickers
        result = resolve_feed_tickers("Dual Mom 126d", ["AAPL", "MSFT"])
        assert result == ["AAPL", "MSFT", "SPY"]

    def test_dual_momentum_no_duplicate_when_spy_selected(self):
        from thermaltrend.dashboard import resolve_feed_tickers
        assert resolve_feed_tickers("Dual Mom 126d", ["AAPL", "SPY"]) == ["AAPL", "SPY"]

    def test_custom_benchmark_from_params(self):
        from thermaltrend.dashboard import resolve_feed_tickers
        result = resolve_feed_tickers("Dual Mom 126d", ["AAPL"], {"benchmark_ticker": "QQQ"})
        assert result == ["AAPL", "QQQ"]

    def test_custom_benchmark_already_present(self):
        from thermaltrend.dashboard import resolve_feed_tickers
        result = resolve_feed_tickers(
            "Dual Mom 126d", ["QQQ"], {"benchmark_ticker": "QQQ"}
        )
        assert result == ["QQQ"]

    def test_defaults_used_when_params_empty(self):
        from thermaltrend.dashboard import STRATEGY_DEFAULTS, resolve_feed_tickers
        benchmark = STRATEGY_DEFAULTS["Dual Mom 126d"]["benchmark_ticker"]
        result = resolve_feed_tickers("Dual Mom 126d", ["AAPL"], {})
        assert result == ["AAPL", benchmark]

    def test_does_not_mutate_input(self):
        from thermaltrend.dashboard import resolve_feed_tickers
        tickers = ["AAPL"]
        resolve_feed_tickers("Dual Mom 126d", tickers)
        assert tickers == ["AAPL"]


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


class TestDatePresets:
    def test_date_presets_defined(self):
        from thermaltrend.dashboard import DATE_PRESETS
        expected_keys = {"1M", "3M", "6M", "1Y", "3Y", "5Y", "10Y", "Max"}
        assert set(DATE_PRESETS.keys()) == expected_keys

    def test_max_is_none(self):
        from thermaltrend.dashboard import DATE_PRESETS
        assert DATE_PRESETS["Max"] is None

    def test_presets_are_positive_integers(self):
        from thermaltrend.dashboard import DATE_PRESETS
        for key, val in DATE_PRESETS.items():
            if val is not None:
                assert isinstance(val, int) and val > 0, f"{key} has invalid value {val}"


class TestLoadTickerData:
    def test_loads_existing_ticker(self):
        from thermaltrend.dashboard import _load_ticker_data, DEFAULT_DATA_DIR
        import os
        tickers = [f.stem for f in Path(DEFAULT_DATA_DIR).glob("*.parquet") if f.stem != "SPY"]
        if tickers:
            df = _load_ticker_data(tickers[0])
            assert not df.empty
            assert "Close" in df.columns

    def test_missing_ticker_returns_empty(self):
        from thermaltrend.dashboard import _load_ticker_data
        df = _load_ticker_data("NONEXISTENT_TICKER_XYZ")
        assert df.empty


class TestFilterByPreset:
    def test_max_returns_full_data(self):
        from thermaltrend.dashboard import _filter_by_preset
        dates = pd.bdate_range("2020-01-01", periods=1000)
        df = pd.DataFrame({"Close": range(1000)}, index=dates)
        result = _filter_by_preset(df, "Max")
        assert len(result) == 1000

    def test_1m_returns_approx_30_rows(self):
        from thermaltrend.dashboard import _filter_by_preset
        dates = pd.bdate_range("2025-01-01", periods=500)
        df = pd.DataFrame({"Close": range(500)}, index=dates)
        result = _filter_by_preset(df, "1M")
        assert len(result) <= 35
        assert len(result) >= 15

    def test_empty_df_returns_empty(self):
        from thermaltrend.dashboard import _filter_by_preset
        df = pd.DataFrame()
        result = _filter_by_preset(df, "1Y")
        assert result.empty


class TestRunStrategyAnalysisSignals:
    """Verify that run_strategy_analysis now includes signals in the result dict."""

    def test_result_contains_signals(self, tmp_path):
        dates = pd.bdate_range("2026-01-01", periods=60)
        closes = [100 + i * (0.5 if i % 8 < 4 else -0.3) for i in range(60)]
        _make_parquet(tmp_path, "TEST", list(zip(dates, closes)))

        feed = DataFeed(str(tmp_path), tickers=["TEST"], start_date="2026-01-01")
        strategy = DonchianBreakoutStrategy(entry_period=10, exit_period=5)
        engine = DataEngine(feed, strategy)
        signals = engine.run()

        result = run_strategy_analysis(signals, feed._data, "Donchian")

        assert "signals" in result
        assert isinstance(result["signals"], list)
        assert len(result["signals"]) == len(signals)


class TestCustomCss:
    """The dashboard injects a forced dark palette via CUSTOM_CSS. These tests
    pin the sidebar readability rules so text never renders dark-on-dark."""

    @staticmethod
    def _hex_to_rgb(hex_color):
        hex_color = hex_color.lstrip("#")
        if len(hex_color) == 3:
            hex_color = "".join(c * 2 for c in hex_color)
        return tuple(int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))

    @staticmethod
    def _luminance(hex_color):
        def linearize(c):
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        r, g, b = (linearize(c) for c in TestCustomCss._hex_to_rgb(hex_color))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    @classmethod
    def _contrast_ratio(cls, a, b):
        la, lb = sorted((cls._luminance(a), cls._luminance(b)), reverse=True)
        return (la + 0.05) / (lb + 0.05)

    def test_sidebar_rule_present(self):
        from thermaltrend.dashboard import CUSTOM_CSS
        assert '[data-testid="stSidebar"]' in CUSTOM_CSS

    def test_sidebar_background_color_set(self):
        from thermaltrend.dashboard import CUSTOM_CSS
        import re
        match = re.search(r'background-color:\s*(#[0-9a-fA-F]{3,8})', CUSTOM_CSS)
        assert match is not None, "no explicit sidebar background color"
        assert TestCustomCss._luminance(match.group(1)) < 0.2, "sidebar bg must be dark"

    def test_sidebar_text_color_set(self):
        from thermaltrend.dashboard import CUSTOM_CSS
        import re
        match = re.search(r'\[data-testid="stSidebarContent"\][^{]*\{\s*color:\s*(#[0-9a-fA-F]{3,8})', CUSTOM_CSS)
        assert match is not None, "no explicit sidebar text color"
        assert TestCustomCss._luminance(match.group(1)) > 0.5, "sidebar text must be light"

    def test_sidebar_text_forced_on_all_children(self):
        from thermaltrend.dashboard import CUSTOM_CSS
        assert '[data-testid="stSidebar"] * { color:' in CUSTOM_CSS

    def test_sidebar_contrast_meets_wcag_aa(self):
        from thermaltrend.dashboard import CUSTOM_CSS
        import re
        bg = re.search(r'background-color:\s*(#[0-9a-fA-F]{3,8})', CUSTOM_CSS)
        fg = re.search(r'\[data-testid="stSidebarContent"\][^{]*\{\s*color:\s*(#[0-9a-fA-F]{3,8})', CUSTOM_CSS)
        ratio = TestCustomCss._contrast_ratio(bg.group(1), fg.group(1))
        assert ratio >= 4.5, f"sidebar contrast {ratio:.2f}:1 below WCAG AA (4.5:1)"

    def test_dark_color_scheme_forced(self):
        from thermaltrend.dashboard import CUSTOM_CSS
        assert "color-scheme: dark" in CUSTOM_CSS

    def test_widget_surfaces_forced_dark(self):
        from thermaltrend.dashboard import CUSTOM_CSS
        assert '[data-testid="stSidebar"] [data-baseweb="select"]' in CUSTOM_CSS
        assert "background-color: #1b1f27 !important" in CUSTOM_CSS

    def test_widget_contrast_meets_wcag_aa(self):
        from thermaltrend.dashboard import CUSTOM_CSS
        import re
        fg = re.search(
            r'\[data-testid="stSidebarContent"\][^{]*\{\s*color:\s*(#[0-9a-fA-F]{3,8})',
            CUSTOM_CSS,
        )
        ratio = TestCustomCss._contrast_ratio("#1b1f27", fg.group(1))
        assert ratio >= 4.5, f"widget contrast {ratio:.2f}:1 below WCAG AA (4.5:1)"


class TestThemeConfig:
    """The .streamlit/config.toml theme must declare base = "dark" so widget
    surfaces (selectboxes, date inputs, etc.) render dark rather than white."""

    def _config_path(self):
        return Path(__file__).resolve().parent.parent.parent / ".streamlit" / "config.toml"

    def test_theme_base_is_dark(self):
        import re
        assert self._config_path().exists(), "missing .streamlit/config.toml"
        text = self._config_path().read_text()
        assert re.search(r'^\s*base\s*=\s*"dark"', text, re.MULTILINE)

    def test_dark_palette_configured(self):
        text = self._config_path().read_text()
        assert "backgroundColor = \"#0e1117\"" in text
        assert "textColor = \"#e0e0e0\"" in text
