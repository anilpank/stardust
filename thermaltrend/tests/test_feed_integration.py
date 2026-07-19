"""Integration tests that use real Parquet data from thermaltrend/data/equities/."""

import subprocess
import sys
from pathlib import Path

import pytest

from feed import DataFeed

THERMALTREND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = THERMALTREND_DIR / "data" / "equities"
FEED_SCRIPT = THERMALTREND_DIR / "feed.py"


@pytest.mark.slow
class TestFeedIntegration:
    def test_loads_real_data(self):
        feed = DataFeed(DATA_DIR)

        assert len(feed.tickers) > 400
        assert len(feed) > 1_000_000

    def test_filter_by_tickers(self):
        feed = DataFeed(DATA_DIR, tickers=["AAPL", "MSFT"])

        assert sorted(feed.tickers) == ["AAPL", "MSFT"]
        assert len(feed) > 0

    def test_filter_by_date_range(self):
        feed = DataFeed(DATA_DIR, tickers=["AAPL"], start_date="2024-01-01", end_date="2024-12-31")

        assert feed.dates[0] >= pytest.importorskip("datetime").datetime(2024, 1, 1)
        assert feed.dates[-1] <= pytest.importorskip("datetime").datetime(2024, 12, 31)

    def test_get_bars_for_date(self):
        feed = DataFeed(DATA_DIR, tickers=["AAPL", "MSFT"])

        bars = feed.get_bars_for_date("2024-01-02")
        assert len(bars) == 2
        tickers = {b.ticker for b in bars}
        assert tickers == {"AAPL", "MSFT"}

    def test_get_ticker_history(self):
        feed = DataFeed(DATA_DIR, tickers=["AAPL"])

        df = feed.get_ticker_history("AAPL")
        assert len(df) > 0
        assert "Close" in df.columns

    def test_chronological_order(self):
        feed = DataFeed(DATA_DIR, tickers=["AAPL", "MSFT"])

        dates = [bar.date for bar in feed]
        assert dates == sorted(dates)


@pytest.mark.slow
class TestFeedCLIIntegration:
    def test_summary(self):
        result = subprocess.run(
            [sys.executable, str(FEED_SCRIPT), "--data-dir", str(DATA_DIR)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "tickers" in result.stdout
        assert "bars" in result.stdout

    def test_tickers_filter(self):
        result = subprocess.run(
            [sys.executable, str(FEED_SCRIPT), "--data-dir", str(DATA_DIR),
             "--tickers", "AAPL", "MSFT"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "2 tickers" in result.stdout

    def test_date_filter(self):
        result = subprocess.run(
            [sys.executable, str(FEED_SCRIPT), "--data-dir", str(DATA_DIR),
             "--date", "2024-01-02"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "Bars for 2024-01-02" in result.stdout
        assert "AAPL" in result.stdout

    def test_ticker_history(self):
        result = subprocess.run(
            [sys.executable, str(FEED_SCRIPT), "--data-dir", str(DATA_DIR),
             "--ticker-history", "AAPL"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "AAPL history" in result.stdout

    def test_head(self):
        result = subprocess.run(
            [sys.executable, str(FEED_SCRIPT), "--data-dir", str(DATA_DIR),
             "--head", "3"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "First 3 bars" in result.stdout
