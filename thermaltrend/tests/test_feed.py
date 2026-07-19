from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from feed import Bar, DataFeed


@pytest.fixture
def sample_ohlcv():
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    return pd.DataFrame(
        {
            "Open": [150.0, 151.0, 152.0, 153.0, 154.0],
            "High": [155.0, 156.0, 157.0, 158.0, 159.0],
            "Low": [145.0, 146.0, 147.0, 148.0, 149.0],
            "Close": [152.0, 153.0, 154.0, 155.0, 156.0],
            "Volume": [1_000_000] * 5,
        },
        index=dates,
    )


def _write_parquet(tmp_path: Path, ticker: str, df: pd.DataFrame) -> Path:
    out = tmp_path / f"{ticker}.parquet"
    df.to_parquet(out)
    return out


class TestDataFeedLoad:
    def test_loads_all_parquet_files(self, tmp_path, sample_ohlcv):
        for ticker in ["AAPL", "MSFT"]:
            _write_parquet(tmp_path, ticker, sample_ohlcv)

        feed = DataFeed(tmp_path)

        assert len(feed.tickers) == 2
        assert sorted(feed.tickers) == ["AAPL", "MSFT"]

    def test_loads_empty_directory(self, tmp_path):
        feed = DataFeed(tmp_path)

        assert len(feed) == 0
        assert feed.tickers == []
        assert feed.dates == []

    def test_filters_by_tickers(self, tmp_path, sample_ohlcv):
        for ticker in ["AAPL", "MSFT", "GOOGL"]:
            _write_parquet(tmp_path, ticker, sample_ohlcv)

        feed = DataFeed(tmp_path, tickers=["AAPL", "MSFT"])

        assert sorted(feed.tickers) == ["AAPL", "MSFT"]
        assert len(feed) == 10

    def test_skips_missing_tickers(self, tmp_path, sample_ohlcv):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)

        feed = DataFeed(tmp_path, tickers=["AAPL", "NOSUCH"])

        assert feed.tickers == ["AAPL"]
        assert len(feed) == 5

    def test_filters_by_start_date(self, tmp_path):
        dates = pd.date_range("2024-01-01", periods=5, freq="D")
        df = pd.DataFrame(
            {"Open": [1.0] * 5, "High": [2.0] * 5, "Low": [0.5] * 5,
             "Close": [1.5] * 5, "Volume": [100] * 5},
            index=dates,
        )
        _write_parquet(tmp_path, "AAPL", df)

        feed = DataFeed(tmp_path, start_date="2024-01-03")

        assert len(feed) == 3
        assert feed.dates[0] == pd.Timestamp("2024-01-03").to_pydatetime()

    def test_filters_by_end_date(self, tmp_path):
        dates = pd.date_range("2024-01-01", periods=5, freq="D")
        df = pd.DataFrame(
            {"Open": [1.0] * 5, "High": [2.0] * 5, "Low": [0.5] * 5,
             "Close": [1.5] * 5, "Volume": [100] * 5},
            index=dates,
        )
        _write_parquet(tmp_path, "AAPL", df)

        feed = DataFeed(tmp_path, end_date="2024-01-03")

        assert len(feed) == 3
        assert feed.dates[-1] == pd.Timestamp("2024-01-03").to_pydatetime()

    def test_filters_by_date_range(self, tmp_path):
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        df = pd.DataFrame(
            {"Open": [1.0] * 10, "High": [2.0] * 10, "Low": [0.5] * 10,
             "Close": [1.5] * 10, "Volume": [100] * 10},
            index=dates,
        )
        _write_parquet(tmp_path, "AAPL", df)

        feed = DataFeed(tmp_path, start_date="2024-01-03", end_date="2024-01-07")

        assert len(feed) == 5


class TestDataFeedIteration:
    def test_yields_bars(self, tmp_path, sample_ohlcv):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)

        feed = DataFeed(tmp_path)
        bars = list(feed)

        assert len(bars) == 5
        assert all(isinstance(b, Bar) for b in bars)

    def test_chronological_order_single_ticker(self, tmp_path, sample_ohlcv):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)

        feed = DataFeed(tmp_path)
        dates = [bar.date for bar in feed]

        assert dates == sorted(dates)

    def test_chronological_order_multiple_tickers(self, tmp_path):
        dates1 = pd.date_range("2024-01-01", periods=3, freq="D")
        dates2 = pd.date_range("2024-01-02", periods=3, freq="D")
        df1 = pd.DataFrame(
            {"Open": [1.0] * 3, "High": [2.0] * 3, "Low": [0.5] * 3,
             "Close": [1.5] * 3, "Volume": [100] * 3},
            index=dates1,
        )
        df2 = pd.DataFrame(
            {"Open": [10.0] * 3, "High": [11.0] * 3, "Low": [9.0] * 3,
             "Close": [10.5] * 3, "Volume": [200] * 3},
            index=dates2,
        )
        _write_parquet(tmp_path, "AAA", df1)
        _write_parquet(tmp_path, "BBB", df2)

        feed = DataFeed(tmp_path)
        bars = list(feed)

        dates = [bar.date for bar in bars]
        assert dates == sorted(dates)

    def test_bar_fields(self, tmp_path, sample_ohlcv):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)

        feed = DataFeed(tmp_path)
        bar = next(iter(feed))

        assert bar.ticker == "AAPL"
        assert isinstance(bar.date, datetime)
        assert bar.open == 150.0
        assert bar.high == 155.0
        assert bar.low == 145.0
        assert bar.close == 152.0
        assert bar.volume == 1_000_000


class TestDataFeedGetBarsForDate:
    def test_returns_bars_for_date(self, tmp_path):
        dates1 = pd.date_range("2024-01-01", periods=3, freq="D")
        dates2 = pd.date_range("2024-01-01", periods=3, freq="D")
        df1 = pd.DataFrame(
            {"Open": [1.0] * 3, "High": [2.0] * 3, "Low": [0.5] * 3,
             "Close": [1.5] * 3, "Volume": [100] * 3},
            index=dates1,
        )
        df2 = pd.DataFrame(
            {"Open": [10.0] * 3, "High": [11.0] * 3, "Low": [9.0] * 3,
             "Close": [10.5] * 3, "Volume": [200] * 3},
            index=dates2,
        )
        _write_parquet(tmp_path, "AAPL", df1)
        _write_parquet(tmp_path, "MSFT", df2)

        feed = DataFeed(tmp_path)
        bars = feed.get_bars_for_date("2024-01-01")

        assert len(bars) == 2
        tickers = {b.ticker for b in bars}
        assert tickers == {"AAPL", "MSFT"}

    def test_returns_empty_for_missing_date(self, tmp_path, sample_ohlcv):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)

        feed = DataFeed(tmp_path)
        bars = feed.get_bars_for_date("2099-01-01")

        assert bars == []

    def test_accepts_datetime_object(self, tmp_path, sample_ohlcv):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)

        feed = DataFeed(tmp_path)
        bars = feed.get_bars_for_date(datetime(2024, 1, 1))

        assert len(bars) == 1
        assert bars[0].ticker == "AAPL"


class TestDataFeedGetTickerHistory:
    def test_returns_dataframe(self, tmp_path, sample_ohlcv):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)

        feed = DataFeed(tmp_path)
        df = feed.get_ticker_history("AAPL")

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 5
        assert all(col in df.columns for col in ["Open", "High", "Low", "Close", "Volume"])

    def test_returns_empty_for_missing_ticker(self, tmp_path, sample_ohlcv):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)

        feed = DataFeed(tmp_path)
        df = feed.get_ticker_history("NOSUCH")

        assert df.empty


class TestDataFeedProperties:
    def test_shape(self, tmp_path, sample_ohlcv):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)
        _write_parquet(tmp_path, "MSFT", sample_ohlcv)

        feed = DataFeed(tmp_path)

        assert feed.shape == (10, 5)

    def test_repr(self, tmp_path, sample_ohlcv):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)

        feed = DataFeed(tmp_path)
        r = repr(feed)

        assert "1 tickers" in r
        assert "5 dates" in r
        assert "5 bars" in r

    def test_len(self, tmp_path, sample_ohlcv):
        for ticker in ["AAPL", "MSFT", "GOOGL"]:
            _write_parquet(tmp_path, ticker, sample_ohlcv)

        feed = DataFeed(tmp_path)

        assert len(feed) == 15
