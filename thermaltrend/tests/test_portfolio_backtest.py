"""Tests for thermaltrend.portfolio_backtest."""

import numpy as np
import pandas as pd
import pytest

from thermaltrend.portfolio.config import PortfolioConfig
from thermaltrend.portfolio_backtest import _print_summary, run_portfolio_backtest


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
    dates = pd.bdate_range("2025-01-02", periods=80)
    wave = 100.0 + 10.0 * np.sin(np.arange(80) / 5.0)
    _make_parquet(tmp_path, "TEST", dates, [float(c) for c in wave])
    _make_parquet(
        tmp_path, "SPY", dates, [float(c) for c in 400.0 + 20.0 * np.sin(np.arange(80) / 5.0)]
    )
    return tmp_path


class TestRunPortfolioBacktest:
    def test_unknown_strategy_raises(self, data_dir):
        with pytest.raises(ValueError, match="Unknown strategy"):
            run_portfolio_backtest("nonexistent", ["TEST"], data_dir=str(data_dir))

    def test_empty_data_raises(self, tmp_path):
        with pytest.raises(ValueError, match="No data found"):
            run_portfolio_backtest(
                "ma_crossover", ["MISSING"], data_dir=str(tmp_path)
            )

    def test_smoke_run(self, data_dir):
        result = run_portfolio_backtest(
            "ma_crossover", ["TEST"], start_date="2025-01-02", data_dir=str(data_dir)
        )
        assert result.metrics["equity_start"] == 10_000.0
        assert result.config.capital == 10_000.0
        assert len(result.daily) == 80
        assert len(result.core_history) == 80
        assert result.metrics["band_violations"] == 0
        assert result.metrics["max_exposure"] <= result.config.band_max + 1e-6
        assert result.equity_curve.iloc[0] == 10_000.0
        assert isinstance(result.trades, list)

    def test_capital_override(self, data_dir):
        result = run_portfolio_backtest(
            "ma_crossover",
            ["TEST"],
            start_date="2025-01-02",
            data_dir=str(data_dir),
            capital=20_000.0,
        )
        assert result.config.capital == 20_000.0
        assert result.metrics["equity_start"] == 20_000.0

    def test_config_argument(self, data_dir):
        cfg = PortfolioConfig(capital=15_000.0, core_enabled=False)
        result = run_portfolio_backtest(
            "donchian",
            ["TEST"],
            start_date="2025-01-02",
            data_dir=str(data_dir),
            config=cfg,
        )
        assert result.config.capital == 15_000.0
        assert result.config.core_enabled is False
        assert result.core_history == []
        assert result.metrics["core_trades"] == 0

    def test_custom_params(self, data_dir):
        result = run_portfolio_backtest(
            "ma_crossover",
            ["TEST"],
            start_date="2025-01-02",
            data_dir=str(data_dir),
            params={"fast_period": 3, "slow_period": 8},
        )
        assert result.metrics["equity_start"] == 10_000.0

    def test_core_ticker_deduped_from_tickers(self, data_dir):
        result = run_portfolio_backtest(
            "ma_crossover",
            ["TEST", "SPY"],
            start_date="2025-01-02",
            data_dir=str(data_dir),
        )
        assert isinstance(result.trades, list)


class TestSatelliteControl:
    def test_no_satellite_skips_stock_signals(self, data_dir):
        result = run_portfolio_backtest(
            satellite=False, start_date="2025-01-02", data_dir=str(data_dir)
        )
        assert result.metrics["equity_start"] == 10_000.0
        assert result.metrics["satellite_trades"] == 0
        assert all(t.strategy_id == "core" for t in result.trades)
        assert result.metrics["core_trades"] == result.metrics["trades_total"]

    def test_no_satellite_needs_no_strategy_or_tickers(self, data_dir):
        result = run_portfolio_backtest(
            strategy_name=None, tickers=None, satellite=False,
            start_date="2025-01-02", data_dir=str(data_dir),
        )
        assert result.metrics["equity_start"] == 10_000.0

    def test_no_satellite_ignores_unknown_strategy(self, data_dir):
        result = run_portfolio_backtest(
            strategy_name="nonexistent", satellite=False,
            start_date="2025-01-02", data_dir=str(data_dir),
        )
        assert result.metrics["trades_total"] >= 0

    def test_satellite_only_control(self, data_dir):
        cfg = PortfolioConfig(core_enabled=False)
        result = run_portfolio_backtest(
            "ma_crossover", ["TEST"], start_date="2025-01-02",
            data_dir=str(data_dir), config=cfg,
        )
        assert result.core_history == []
        assert result.metrics["core_trades"] == 0


class TestPrintSummary:
    def test_prints_summary(self, data_dir, capsys):
        result = run_portfolio_backtest(
            "ma_crossover", ["TEST"], start_date="2025-01-02", data_dir=str(data_dir)
        )
        _print_summary(result, "ma_crossover")
        captured = capsys.readouterr()
        assert "Portfolio system" in captured.out
        assert "ma_crossover" in captured.out
        assert "Equity:" in captured.out
        assert "vs SPY" in captured.out

    def test_prints_control_label(self, data_dir, capsys):
        result = run_portfolio_backtest(
            satellite=False, start_date="2025-01-02", data_dir=str(data_dir)
        )
        _print_summary(result, None)
        captured = capsys.readouterr()
        assert "SPY-only control" in captured.out
