"""Tests for thermaltrend/compare_cli.py — multi-strategy comparison CLI."""

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from thermaltrend.compare_cli import STRATEGY_REGISTRY, run_compare


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


def _make_spy(tmp_path, dates_closes):
    """Create SPY parquet for benchmark comparison."""
    df = pd.DataFrame(
        {"Open": [c for _, c in dates_closes],
         "High": [c + 1 for _, c in dates_closes],
         "Low": [c - 1 for _, c in dates_closes],
         "Close": [c for _, c in dates_closes],
         "Volume": [10000] * len(dates_closes),
         "ticker": "SPY"},
        index=pd.DatetimeIndex([d for d, _ in dates_closes], name="Date"),
    )
    df.to_parquet(tmp_path / "SPY.parquet")


class TestRunCompare:
    def test_compare_two_strategies(self, tmp_path):
        dates = pd.bdate_range("2026-01-01", periods=50)
        closes = [100 + i * (1 if i % 10 < 5 else -1) for i in range(50)]
        _make_parquet(tmp_path, "TEST", list(zip(dates, closes)))
        _make_spy(tmp_path, list(zip(dates, closes)))

        ranking = run_compare(
            tickers=["TEST"],
            strategy_names=["ma_crossover", "donchian"],
            start_date="2026-01-01",
            data_dir=str(tmp_path),
        )

        assert len(ranking) >= 2  # strategies + benchmark
        assert "strategy" in ranking.columns
        assert "sharpe" in ranking.columns

    def test_compare_all_strategies(self, tmp_path):
        dates = pd.bdate_range("2026-01-01", periods=50)
        closes = [100 + i * (1 if i % 10 < 5 else -1) for i in range(50)]
        _make_parquet(tmp_path, "TEST", list(zip(dates, closes)))
        _make_spy(tmp_path, list(zip(dates, closes)))

        ranking = run_compare(
            tickers=["TEST"],
            start_date="2026-01-01",
            data_dir=str(tmp_path),
        )

        assert len(ranking) >= len(STRATEGY_REGISTRY)  # all strategies + benchmark

    def test_unknown_strategy_raises(self, tmp_path):
        # Need at least one parquet file so DataFeed is non-empty
        dates = pd.bdate_range("2026-01-01", periods=5)
        _make_parquet(tmp_path, "TEST", list(zip(dates, [100] * 5)))
        with pytest.raises(ValueError, match="Unknown strategy"):
            run_compare(
                tickers=["TEST"],
                strategy_names=["nonexistent"],
                start_date="2026-01-01",
                data_dir=str(tmp_path),
            )

    def test_empty_data_raises(self, tmp_path):
        with pytest.raises(ValueError, match="No data found"):
            run_compare(
                tickers=["NONEXISTENT"],
                strategy_names=["ma_crossover"],
                data_dir=str(tmp_path),
            )

    def test_sort_by_cagr(self, tmp_path):
        dates = pd.bdate_range("2026-01-01", periods=50)
        closes = [100 + i * (1 if i % 10 < 5 else -1) for i in range(50)]
        _make_parquet(tmp_path, "TEST", list(zip(dates, closes)))
        _make_spy(tmp_path, list(zip(dates, closes)))

        ranking = run_compare(
            tickers=["TEST"],
            strategy_names=["ma_crossover", "donchian"],
            start_date="2026-01-01",
            sort_by="cagr",
            data_dir=str(tmp_path),
        )

        assert ranking.index[0] == 1

    def test_benchmark_included(self, tmp_path):
        dates = pd.bdate_range("2026-01-01", periods=50)
        closes = [100 + i * 0.5 for i in range(50)]
        _make_parquet(tmp_path, "TEST", list(zip(dates, closes)))
        _make_spy(tmp_path, list(zip(dates, closes)))

        ranking = run_compare(
            tickers=["TEST"],
            strategy_names=["ma_crossover"],
            start_date="2026-01-01",
            data_dir=str(tmp_path),
        )

        assert "S&P 500 B&H" in ranking["strategy"].values
