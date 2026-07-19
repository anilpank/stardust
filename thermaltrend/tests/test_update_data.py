from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from update_data import get_existing_tickers, update_ticker


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


class TestGetExistingTickers:
    def test_returns_all_parquet_files(self, tmp_path, sample_ohlcv):
        for ticker in ["AAPL", "MSFT", "GOOGL"]:
            _write_parquet(tmp_path, ticker, sample_ohlcv)

        result = get_existing_tickers(tmp_path, None)

        assert sorted(result) == ["AAPL", "GOOGL", "MSFT"]

    def test_filters_by_requested_tickers(self, tmp_path, sample_ohlcv):
        for ticker in ["AAPL", "MSFT", "GOOGL"]:
            _write_parquet(tmp_path, ticker, sample_ohlcv)

        result = get_existing_tickers(tmp_path, ["AAPL", "MSFT"])

        assert sorted(result) == ["AAPL", "MSFT"]

    def test_warns_about_missing_tickers(self, tmp_path, sample_ohlcv, capsys):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)

        get_existing_tickers(tmp_path, ["AAPL", "NOSUCH"])

        captured = capsys.readouterr()
        assert "NOSUCH" in captured.out

    def test_returns_empty_for_no_parquets(self, tmp_path):
        result = get_existing_tickers(tmp_path, None)

        assert result == []

    def test_missing_tickers_not_in_result(self, tmp_path, sample_ohlcv):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)

        result = get_existing_tickers(tmp_path, ["AAPL", "NOSUCH"])

        assert result == ["AAPL"]


class TestUpdateTicker:
    def test_appends_new_data(self, tmp_path, sample_ohlcv):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)

        new_dates = pd.date_range("2024-01-06", periods=3, freq="D")
        new_data = pd.DataFrame(
            {
                "Open": [155.0, 156.0, 157.0],
                "High": [160.0, 161.0, 162.0],
                "Low": [150.0, 151.0, 152.0],
                "Close": [157.0, 158.0, 159.0],
                "Volume": [2_000_000] * 3,
            },
            index=new_dates,
        )

        with patch("update_data.yf") as mock_yf, \
             patch("update_data.pd.Timestamp.now") as mock_now:
            mock_now.return_value = pd.Timestamp("2024-01-09")
            mock_yf.download.return_value = new_data

            status = update_ticker("AAPL", tmp_path)

        assert status == "updated"
        saved = pd.read_parquet(tmp_path / "AAPL.parquet")
        assert len(saved) == 8
        assert saved.index.max() == pd.Timestamp("2024-01-08")

    def test_up_to_date_when_last_is_today(self, tmp_path, sample_ohlcv):
        today = pd.Timestamp.now().normalize()
        today_index = pd.date_range(end=today, periods=5, freq="B")
        df = sample_ohlcv.copy()
        df.index = today_index
        _write_parquet(tmp_path, "AAPL", df)

        status = update_ticker("AAPL", tmp_path)

        assert status == "up_to_date"

    def test_empty_download_returns_up_to_date(self, tmp_path, sample_ohlcv):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)

        with patch("update_data.yf") as mock_yf, \
             patch("update_data.pd.Timestamp.now") as mock_now:
            mock_now.return_value = pd.Timestamp("2024-01-10")
            mock_yf.download.return_value = pd.DataFrame()

            status = update_ticker("AAPL", tmp_path)

        assert status == "up_to_date"

    def test_download_error_returns_failed(self, tmp_path, sample_ohlcv):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)

        with patch("update_data.yf") as mock_yf, \
             patch("update_data.pd.Timestamp.now") as mock_now:
            mock_now.return_value = pd.Timestamp("2024-01-10")
            mock_yf.download.side_effect = Exception("Network error")

            status = update_ticker("AAPL", tmp_path)

        assert status == "failed"

    def test_corrupt_parquet_returns_failed(self, tmp_path):
        corrupt_file = tmp_path / "FAIL.parquet"
        corrupt_file.write_text("not a parquet file")

        status = update_ticker("FAIL", tmp_path)

        assert status == "failed"

    def test_deduplicates_overlapping_data(self, tmp_path, sample_ohlcv):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)

        overlap_dates = pd.date_range("2024-01-04", periods=3, freq="D")
        overlap_data = pd.DataFrame(
            {
                "Open": [200.0, 201.0, 202.0],
                "High": [205.0, 206.0, 207.0],
                "Low": [195.0, 196.0, 197.0],
                "Close": [202.0, 203.0, 204.0],
                "Volume": [3_000_000] * 3,
            },
            index=overlap_dates,
        )

        with patch("update_data.yf") as mock_yf, \
             patch("update_data.pd.Timestamp.now") as mock_now:
            mock_now.return_value = pd.Timestamp("2024-01-09")
            mock_yf.download.return_value = overlap_data

            status = update_ticker("AAPL", tmp_path)

        assert status == "updated"
        saved = pd.read_parquet(tmp_path / "AAPL.parquet")
        assert len(saved) == 6
        assert saved.index.is_unique

    def test_flattens_multiindex_columns(self, tmp_path, sample_ohlcv):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)

        new_dates = pd.date_range("2024-01-06", periods=2, freq="D")
        multi = pd.DataFrame(
            {
                ("Open", "AAPL"): [155.0, 156.0],
                ("High", "AAPL"): [160.0, 161.0],
                ("Low", "AAPL"): [150.0, 151.0],
                ("Close", "AAPL"): [157.0, 158.0],
                ("Volume", "AAPL"): [2_000_000, 2_100_000],
            },
            index=new_dates,
        )
        multi.columns = pd.MultiIndex.from_tuples(
            multi.columns, names=["Price", "Ticker"]
        )

        with patch("update_data.yf") as mock_yf, \
             patch("update_data.pd.Timestamp.now") as mock_now:
            mock_now.return_value = pd.Timestamp("2024-01-09")
            mock_yf.download.return_value = multi

            status = update_ticker("AAPL", tmp_path)

        assert status == "updated"
        saved = pd.read_parquet(tmp_path / "AAPL.parquet")
        assert list(saved.columns) == ["Open", "High", "Low", "Close", "Volume"]

    def test_no_new_rows_after_dedup_returns_up_to_date(self, tmp_path, sample_ohlcv):
        _write_parquet(tmp_path, "AAPL", sample_ohlcv)

        same_dates = pd.date_range("2024-01-01", periods=5, freq="D")
        same_data = pd.DataFrame(
            {
                "Open": [150.0, 151.0, 152.0, 153.0, 154.0],
                "High": [155.0, 156.0, 157.0, 158.0, 159.0],
                "Low": [145.0, 146.0, 147.0, 148.0, 149.0],
                "Close": [152.0, 153.0, 154.0, 155.0, 156.0],
                "Volume": [1_000_000] * 5,
            },
            index=same_dates,
        )

        with patch("update_data.yf") as mock_yf, \
             patch("update_data.pd.Timestamp.now") as mock_now:
            mock_now.return_value = pd.Timestamp("2024-01-09")
            mock_yf.download.return_value = same_data

            status = update_ticker("AAPL", tmp_path)

        assert status == "up_to_date"
