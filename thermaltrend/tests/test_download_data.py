from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from download_data import download_and_save, get_sp500_tickers


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


class TestGetSp500Tickers:
    @patch("download_data.pd.read_html")
    def test_returns_list_of_strings(self, mock_read_html):
        df = pd.DataFrame({"Symbol": ["AAPL", "MSFT", "GOOGL"]})
        mock_read_html.return_value = [df]

        tickers = get_sp500_tickers()

        assert tickers == ["AAPL", "MSFT", "GOOGL"]
        assert all(isinstance(t, str) for t in tickers)

    @patch("download_data.pd.read_html")
    def test_replaces_dots_with_hyphens(self, mock_read_html):
        df = pd.DataFrame({"Symbol": ["BRK.B", "BF.B"]})
        mock_read_html.return_value = [df]

        tickers = get_sp500_tickers()

        assert "BRK-B" in tickers
        assert "BF-B" in tickers

    @patch("download_data.pd.read_html")
    def test_calls_wikipedia_url(self, mock_read_html):
        df = pd.DataFrame({"Symbol": ["AAPL"]})
        mock_read_html.return_value = [df]

        get_sp500_tickers()

        mock_read_html.assert_called_once_with(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        )


class TestDownloadAndSave:
    def test_skips_existing_files(self, tmp_path, sample_ohlcv):
        existing = tmp_path / "AAPL.parquet"
        existing.touch()

        with patch("download_data.yf") as mock_yf:
            download_and_save(["AAPL"], "2024-01-01", "2024-01-06", tmp_path)

            mock_yf.download.assert_not_called()

    @patch("download_data.time.sleep")
    @patch("download_data.yf")
    def test_downloads_and_saves_parquet(self, mock_yf, mock_sleep, tmp_path, sample_ohlcv):
        mock_yf.download.return_value = sample_ohlcv

        download_and_save(["AAPL"], "2024-01-01", "2024-01-06", tmp_path)

        out_file = tmp_path / "AAPL.parquet"
        assert out_file.exists()

        saved = pd.read_parquet(out_file)
        assert len(saved) == 5
        assert "Close" in saved.columns

    @patch("download_data.time.sleep")
    @patch("download_data.yf")
    def test_handles_empty_data(self, mock_yf, mock_sleep, tmp_path):
        mock_yf.download.return_value = pd.DataFrame()

        download_and_save(["NODATA"], "2024-01-01", "2024-01-06", tmp_path)

        assert not (tmp_path / "NODATA.parquet").exists()

    @patch("download_data.time.sleep")
    @patch("download_data.yf")
    def test_handles_download_exception(self, mock_yf, mock_sleep, tmp_path):
        mock_yf.download.side_effect = Exception("Network error")

        download_and_save(["FAIL"], "2024-01-01", "2024-01-06", tmp_path)

        assert not (tmp_path / "FAIL.parquet").exists()

    @patch("download_data.time.sleep")
    @patch("download_data.yf")
    def test_flattens_multiindex_columns(self, mock_yf, mock_sleep, tmp_path):
        dates = pd.date_range("2024-01-01", periods=3, freq="D")
        multi = pd.DataFrame(
            {(("Close", "AAPL"),): [100, 101, 102],
             (("Volume", "AAPL"),): [1000, 1100, 1200]},
            index=dates,
        )
        multi.columns = pd.MultiIndex.from_tuples(
            [("Close", "AAPL"), ("Volume", "AAPL")],
            names=["Price", "Ticker"],
        )
        mock_yf.download.return_value = multi

        download_and_save(["AAPL"], "2024-01-01", "2024-01-04", tmp_path)

        saved = pd.read_parquet(tmp_path / "AAPL.parquet")
        assert list(saved.columns) == ["Close", "Volume"]

    @patch("download_data.time.sleep")
    @patch("download_data.yf")
    def test_creates_output_directory(self, mock_yf, mock_sleep, tmp_path, sample_ohlcv):
        mock_yf.download.return_value = sample_ohlcv
        out_dir = tmp_path / "nested" / "data"

        download_and_save(["AAPL"], "2024-01-01", "2024-01-06", out_dir)

        assert out_dir.exists()
        assert (out_dir / "AAPL.parquet").exists()

    @patch("download_data.time.sleep")
    @patch("download_data.yf")
    def test_multiple_tickers(self, mock_yf, mock_sleep, tmp_path, sample_ohlcv):
        mock_yf.download.return_value = sample_ohlcv

        download_and_save(["AAPL", "MSFT"], "2024-01-01", "2024-01-06", tmp_path)

        assert (tmp_path / "AAPL.parquet").exists()
        assert (tmp_path / "MSFT.parquet").exists()
        assert mock_yf.download.call_count == 2
