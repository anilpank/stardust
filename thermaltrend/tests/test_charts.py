"""Tests for thermaltrend/charts.py — Plotly chart components."""

from datetime import datetime
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from thermaltrend.analytics.trade_simulator import Trade, TradeSimulator
from thermaltrend.charts import (
    COLORS,
    _apply_layout,
    drawdown_chart,
    equity_curve,
    normalized_price_overlay,
    ohlcv_chart,
    per_ticker_bar,
    period_heatmap,
    pnl_distribution,
    price_with_signals,
    regime_bar,
    strategy_comparison_bar,
)
from thermaltrend.core.events import SignalDirection


def _make_trade(
    ticker="AAPL",
    pnl=150.0,
    pnl_pct=0.015,
    holding_days=10,
    exit_reason="signal",
    entry_date=None,
    exit_date=None,
):
    entry_date = entry_date or datetime(2026, 1, 10)
    exit_date = exit_date or datetime(2026, 1, 20)
    return Trade(
        ticker=ticker,
        entry_date=entry_date,
        entry_price=100.0,
        exit_date=exit_date,
        exit_price=100.0 + pnl / 100,
        direction=SignalDirection.BUY,
        pnl=pnl,
        pnl_pct=pnl_pct,
        holding_days=holding_days,
        exit_reason=exit_reason,
        strategy_id="test",
        shares=100,
        stop_price=0.0,
    )


def _make_signal(timestamp=None, ticker="AAPL", direction=SignalDirection.BUY, strength=0.5):
    from thermaltrend.core.events import SignalEvent
    from uuid import uuid4
    return SignalEvent(
        timestamp=timestamp or datetime(2026, 1, 15),
        ticker=ticker,
        direction=direction,
        strength=strength,
        strategy_id="test",
        metadata={},
        id=uuid4(),
    )


def _make_equity_series(values=None):
    if values is None:
        values = [100_000, 100_500, 101_000, 99_000, 100_200]
    dates = pd.bdate_range("2026-01-01", periods=len(values))
    return pd.Series(values, index=dates, dtype=float)


class TestApplyLayout:
    def test_returns_figure(self):
        fig = go.Figure()
        result = _apply_layout(fig, "Test Title")
        assert isinstance(result, go.Figure)

    def test_sets_title(self):
        fig = go.Figure()
        result = _apply_layout(fig, "My Title")
        assert result.layout.title.text == "My Title"

    def test_sets_height(self):
        fig = go.Figure()
        result = _apply_layout(fig, "H", height=250)
        assert result.layout.height == 250

    def test_default_height(self):
        fig = go.Figure()
        result = _apply_layout(fig, "H")
        assert result.layout.height == 400


class TestEquityCurve:
    def test_returns_figure(self):
        eq = _make_equity_series()
        fig = equity_curve(eq)
        assert isinstance(fig, go.Figure)

    def test_has_strategy_trace(self):
        eq = _make_equity_series()
        fig = equity_curve(eq)
        names = [t.name for t in fig.data]
        assert "Strategy" in names

    def test_with_benchmark(self):
        eq = _make_equity_series()
        bench = _make_equity_series([100_000, 100_200, 100_400, 99_800, 100_100])
        fig = equity_curve(eq, benchmark=bench)
        names = [t.name for t in fig.data]
        assert "S&P 500 B&H" in names

    def test_benchmark_none_only_one_trace(self):
        eq = _make_equity_series()
        fig = equity_curve(eq, benchmark=None)
        assert len(fig.data) == 1

    def test_short_benchmark_ignored(self):
        eq = _make_equity_series()
        bench = pd.Series([100.0], index=[pd.Timestamp("2026-01-01")])
        fig = equity_curve(eq, benchmark=bench)
        assert len(fig.data) == 1


class TestDrawdownChart:
    def test_returns_figure(self):
        eq = _make_equity_series()
        fig = drawdown_chart(eq)
        assert isinstance(fig, go.Figure)

    def test_has_one_trace(self):
        eq = _make_equity_series()
        fig = drawdown_chart(eq)
        assert len(fig.data) == 1

    def test_drawdown_values_non_positive(self):
        eq = _make_equity_series([100, 110, 105, 90, 95])
        fig = drawdown_chart(eq)
        y_values = fig.data[0].y
        assert all(v <= 0 for v in y_values)


class TestPnlDistribution:
    def test_returns_figure(self):
        trades = [_make_trade(pnl=100), _make_trade(pnl=-50)]
        fig = pnl_distribution(trades)
        assert isinstance(fig, go.Figure)

    def test_empty_trades_shows_annotation(self):
        fig = pnl_distribution([])
        assert len(fig.layout.annotations) > 0

    def test_only_data_end_trades_shows_annotation(self):
        trades = [_make_trade(exit_reason="data_end")]
        fig = pnl_distribution(trades)
        assert len(fig.layout.annotations) > 0

    def test_completed_trades_have_bar_trace(self):
        trades = [_make_trade(pnl=100), _make_trade(pnl=-50)]
        fig = pnl_distribution(trades)
        assert len(fig.data) == 1
        assert fig.data[0].type == "bar"


class TestPerTickerBar:
    def test_returns_figure(self):
        data = {"AAPL": {"total_pnl": 500, "win_rate": 0.6}, "MSFT": {"total_pnl": -200, "win_rate": 0.4}}
        fig = per_ticker_bar(data)
        assert isinstance(fig, go.Figure)

    def test_empty_completed_trades(self):
        data = {"AAPL": {"status": "no_completed_trades"}}
        fig = per_ticker_bar(data)
        assert len(fig.layout.annotations) > 0

    def test_sorted_by_pnl_descending(self):
        data = {
            "LOSER": {"total_pnl": -100},
            "WINNER": {"total_pnl": 500},
            "MID": {"total_pnl": 100},
        }
        fig = per_ticker_bar(data, metric="total_pnl")
        x_values = list(fig.data[0].x)
        assert x_values == ["WINNER", "MID", "LOSER"]


class TestRegimeBar:
    def test_returns_figure(self):
        data = {
            "bull": {"total_trades": 10, "total_pnl": 500},
            "bear": {"total_trades": 5, "total_pnl": -200},
            "sideways": {"total_trades": 3, "total_pnl": 50},
        }
        fig = regime_bar(data)
        assert isinstance(fig, go.Figure)

    def test_empty_regimes(self):
        fig = regime_bar({})
        assert len(fig.layout.annotations) > 0

    def test_only_active_regimes(self):
        data = {
            "bull": {"total_trades": 10, "total_pnl": 500},
            "bear": {"total_trades": 0, "total_pnl": 0},
            "sideways": {"total_trades": 0, "total_pnl": 0},
        }
        fig = regime_bar(data)
        assert len(fig.data) == 1
        assert len(fig.data[0].x) == 1
        assert fig.data[0].x[0] == "BULL"


class TestStrategyComparisonBar:
    def test_returns_figure(self):
        df = pd.DataFrame({"strategy": ["A", "B"], "sharpe": [1.2, 0.8]})
        fig = strategy_comparison_bar(df, "sharpe")
        assert isinstance(fig, go.Figure)

    def test_empty_df(self):
        fig = strategy_comparison_bar(pd.DataFrame(), "sharpe")
        assert len(fig.layout.annotations) > 0

    def test_benchmark_row_colored_grey(self):
        df = pd.DataFrame({
            "strategy": ["Strat A", "S&P 500 B&H"],
            "sharpe": [1.2, 0.5],
        })
        fig = strategy_comparison_bar(df, "sharpe")
        marker_colors = fig.data[0].marker.color
        assert marker_colors[1] == COLORS["grey"]


class TestPeriodHeatmap:
    def test_returns_figure(self):
        data = {"2026-01": {"total_pnl": 200}, "2026-02": {"total_pnl": -100}}
        fig = period_heatmap(data)
        assert isinstance(fig, go.Figure)

    def test_empty_data(self):
        fig = period_heatmap({})
        assert len(fig.layout.annotations) > 0

    def test_chronological_order(self):
        data = {"2026-03": {"total_pnl": 300}, "2026-01": {"total_pnl": 100}, "2026-02": {"total_pnl": 200}}
        fig = period_heatmap(data)
        x_values = list(fig.data[0].x)
        assert x_values == ["2026-01", "2026-02", "2026-03"]


class TestPriceWithSignals:
    def _make_ticker_df(self, dates):
        n = len(dates)
        return pd.DataFrame({
            "Open": [100 + i for i in range(n)],
            "High": [101 + i for i in range(n)],
            "Low": [99 + i for i in range(n)],
            "Close": [100 + i for i in range(n)],
            "Volume": [1_000_000] * n,
        }, index=dates)

    def test_returns_figure_with_candlestick(self):
        dates = pd.bdate_range("2026-01-01", periods=10)
        df = self._make_ticker_df(dates)
        fig = price_with_signals(df, signals=[])
        assert isinstance(fig, go.Figure)
        trace_types = [t.type for t in fig.data]
        assert "candlestick" in trace_types
        assert "bar" in trace_types

    def test_buy_signals_add_markers(self):
        dates = pd.bdate_range("2026-01-01", periods=10)
        df = self._make_ticker_df(dates)
        signals = [_make_signal(timestamp=datetime(2026, 1, 5), direction=SignalDirection.BUY)]
        fig = price_with_signals(df, signals)
        names = [t.name for t in fig.data]
        assert "BUY" in names

    def test_sell_signals_add_markers(self):
        dates = pd.bdate_range("2026-01-01", periods=10)
        df = self._make_ticker_df(dates)
        signals = [_make_signal(timestamp=datetime(2026, 1, 5), direction=SignalDirection.SELL)]
        fig = price_with_signals(df, signals)
        names = [t.name for t in fig.data]
        assert "SELL" in names


class TestColors:
    def test_all_expected_colors_defined(self):
        expected = {"blue", "green", "red", "orange", "purple", "cyan", "pink", "grey", "bull", "bear", "sideways"}
        assert expected == set(COLORS.keys())

    def test_colors_are_hex_strings(self):
        for name, color in COLORS.items():
            assert color.startswith("#"), f"{name} is not a hex color"
            assert len(color) == 7, f"{name} hex color has wrong length"


class TestOhlcvChart:
    def _make_ticker_df(self, dates):
        n = len(dates)
        return pd.DataFrame({
            "Open": [100 + i for i in range(n)],
            "High": [101 + i for i in range(n)],
            "Low": [99 + i for i in range(n)],
            "Close": [100 + i for i in range(n)],
            "Volume": [1_000_000] * n,
        }, index=dates)

    def test_returns_figure_with_candlestick_and_volume(self):
        dates = pd.bdate_range("2026-01-01", periods=10)
        df = self._make_ticker_df(dates)
        fig = ohlcv_chart(df, "TEST")
        assert isinstance(fig, go.Figure)
        trace_types = [t.type for t in fig.data]
        assert "candlestick" in trace_types
        assert "bar" in trace_types

    def test_title_includes_ticker_name(self):
        dates = pd.bdate_range("2026-01-01", periods=5)
        df = self._make_ticker_df(dates)
        fig = ohlcv_chart(df, "AAPL")
        assert "AAPL" in fig.layout.title.text

    def test_custom_title(self):
        dates = pd.bdate_range("2026-01-01", periods=5)
        df = self._make_ticker_df(dates)
        fig = ohlcv_chart(df, "MSFT", title="Custom Title")
        assert fig.layout.title.text == "Custom Title"

    def test_height_is_500(self):
        dates = pd.bdate_range("2026-01-01", periods=5)
        df = self._make_ticker_df(dates)
        fig = ohlcv_chart(df, "TEST")
        assert fig.layout.height == 500


class TestNormalizedPriceOverlay:
    def _make_ticker_df(self, dates, base_price=100):
        n = len(dates)
        return pd.DataFrame({
            "Open": [base_price + i for i in range(n)],
            "High": [base_price + i + 1 for i in range(n)],
            "Low": [base_price + i - 1 for i in range(n)],
            "Close": [base_price + i for i in range(n)],
            "Volume": [1_000_000] * n,
        }, index=dates)

    def test_returns_figure(self):
        dates = pd.bdate_range("2026-01-01", periods=10)
        dfs = {"AAPL": self._make_ticker_df(dates, 100), "MSFT": self._make_ticker_df(dates, 200)}
        fig = normalized_price_overlay(dfs)
        assert isinstance(fig, go.Figure)

    def test_all_start_at_100(self):
        dates = pd.bdate_range("2026-01-01", periods=10)
        dfs = {"AAPL": self._make_ticker_df(dates, 100), "MSFT": self._make_ticker_df(dates, 200)}
        fig = normalized_price_overlay(dfs)
        for trace in fig.data:
            assert trace.y[0] == 100.0

    def test_one_ticker(self):
        dates = pd.bdate_range("2026-01-01", periods=5)
        dfs = {"AAPL": self._make_ticker_df(dates)}
        fig = normalized_price_overlay(dfs)
        assert len(fig.data) == 1

    def test_empty_df_skipped(self):
        dates = pd.bdate_range("2026-01-01", periods=5)
        dfs = {"AAPL": self._make_ticker_df(dates), "EMPTY": pd.DataFrame()}
        fig = normalized_price_overlay(dfs)
        assert len(fig.data) == 1

    def test_legend_names_match_tickers(self):
        dates = pd.bdate_range("2026-01-01", periods=5)
        dfs = {"AAPL": self._make_ticker_df(dates), "MSFT": self._make_ticker_df(dates)}
        fig = normalized_price_overlay(dfs)
        names = [t.name for t in fig.data]
        assert "AAPL" in names
        assert "MSFT" in names

    def test_default_title(self):
        dates = pd.bdate_range("2026-01-01", periods=5)
        dfs = {"A": self._make_ticker_df(dates)}
        fig = normalized_price_overlay(dfs)
        assert "Normalized" in fig.layout.title.text
