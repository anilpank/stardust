from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from thermaltrend.download_data import download_and_save, get_sp500_constituents, get_sp500_tickers, get_universe


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


@pytest.fixture
def sample_wikipedia_table():
    return pd.DataFrame(
        {
            "Symbol": ["AAPL", "MSFT", "GOOGL", "BRK.B"],
            "Date added": ["1982-11-30", "1976-03-31", "2006-04-03", "2010-02-16"],
        }
    )


class TestGetSp500Constituents:
    @patch("thermaltrend.download_data.pd.read_html")
    @patch("thermaltrend.download_data.requests.get")
    def test_returns_dataframe_with_expected_columns(self, mock_get, mock_read_html, sample_wikipedia_table):
        mock_get.return_value = MagicMock(text="<html></html>")
        mock_read_html.return_value = [sample_wikipedia_table]

        result = get_sp500_constituents()

        assert list(result.columns) == ["ticker", "date_added"]
        assert len(result) == 4

    @patch("thermaltrend.download_data.pd.read_html")
    @patch("thermaltrend.download_data.requests.get")
    def test_replaces_dots_with_hyphens(self, mock_get, mock_read_html, sample_wikipedia_table):
        mock_get.return_value = MagicMock(text="<html></html>")
        mock_read_html.return_value = [sample_wikipedia_table]

        result = get_sp500_constituents()

        assert "BRK-B" in result["ticker"].tolist()
        assert "BRK.B" not in result["ticker"].tolist()

    @patch("thermaltrend.download_data.pd.read_html")
    @patch("thermaltrend.download_data.requests.get")
    def test_calls_wikipedia_url(self, mock_get, mock_read_html, sample_wikipedia_table):
        mock_get.return_value = MagicMock(text="<html></html>")
        mock_read_html.return_value = [sample_wikipedia_table]

        get_sp500_constituents()

        mock_get.assert_called_once_with(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": "Mozilla/5.0"},
        )

    @patch("thermaltrend.download_data.pd.read_html")
    @patch("thermaltrend.download_data.requests.get")
    def test_preserves_date_added(self, mock_get, mock_read_html, sample_wikipedia_table):
        mock_get.return_value = MagicMock(text="<html></html>")
        mock_read_html.return_value = [sample_wikipedia_table]

        result = get_sp500_constituents()

        assert result.loc[result["ticker"] == "AAPL", "date_added"].iloc[0] == "1982-11-30"
        assert result.loc[result["ticker"] == "GOOGL", "date_added"].iloc[0] == "2006-04-03"


class TestGetSp500Tickers:
    @patch("thermaltrend.download_data.get_sp500_constituents")
    def test_returns_list_of_strings(self, mock_constituents):
        mock_constituents.return_value = pd.DataFrame(
            {"ticker": ["AAPL", "MSFT", "GOOGL"], "date_added": ["1982-11-30", "1976-03-31", "2006-04-03"]}
        )

        tickers = get_sp500_tickers()

        assert tickers == ["AAPL", "MSFT", "GOOGL"]
        assert all(isinstance(t, str) for t in tickers)

    @patch("thermaltrend.download_data.get_sp500_constituents")
    def test_delegates_to_constituents(self, mock_constituents):
        mock_constituents.return_value = pd.DataFrame(
            {"ticker": ["AAPL"], "date_added": ["1982-11-30"]}
        )

        get_sp500_tickers()

        mock_constituents.assert_called_once()


class TestGetUniverse:
    def test_filters_by_date_added(self, tmp_path):
        csv_content = "ticker,date_added\nAAPL,1982-11-30\nGOOGL,2006-04-03\nAMZN,2005-11-18\n"
        (tmp_path / "constituents.csv").write_text(csv_content)

        result = get_universe(tmp_path, "2006-01-01")

        assert "AAPL" in result
        assert "AMZN" in result
        assert "GOOGL" not in result

    def test_includes_tickers_added_on_exact_date(self, tmp_path):
        csv_content = "ticker,date_added\nAAPL,1982-11-30\nGOOGL,2006-04-03\n"
        (tmp_path / "constituents.csv").write_text(csv_content)

        result = get_universe(tmp_path, "2006-04-03")

        assert "GOOGL" in result

    def test_early_date_returns_original_constituents(self, tmp_path):
        csv_content = "ticker,date_added\nMMM,1957-03-04\nAAPL,1982-11-30\nGOOGL,2006-04-03\n"
        (tmp_path / "constituents.csv").write_text(csv_content)

        result = get_universe(tmp_path, "1970-01-01")

        assert "MMM" in result
        assert "AAPL" not in result
        assert "GOOGL" not in result


class TestDownloadAndSave:
    def test_skips_existing_files(self, tmp_path, sample_ohlcv):
        existing = tmp_path / "AAPL.parquet"
        existing.touch()

        with patch("thermaltrend.download_data.yf") as mock_yf:
            download_and_save(["AAPL"], "2024-01-01", "2024-01-06", tmp_path)

            mock_yf.download.assert_not_called()

    @patch("thermaltrend.download_data.time.sleep")
    @patch("thermaltrend.download_data.yf")
    def test_downloads_and_saves_parquet(self, mock_yf, mock_sleep, tmp_path, sample_ohlcv):
        mock_yf.download.return_value = sample_ohlcv

        download_and_save(["AAPL"], "2024-01-01", "2024-01-06", tmp_path)

        out_file = tmp_path / "AAPL.parquet"
        assert out_file.exists()

        saved = pd.read_parquet(out_file)
        assert len(saved) == 5
        assert "Close" in saved.columns

    @patch("thermaltrend.download_data.time.sleep")
    @patch("thermaltrend.download_data.yf")
    def test_handles_empty_data(self, mock_yf, mock_sleep, tmp_path):
        mock_yf.download.return_value = pd.DataFrame()

        download_and_save(["NODATA"], "2024-01-01", "2024-01-06", tmp_path)

        assert not (tmp_path / "NODATA.parquet").exists()

    @patch("thermaltrend.download_data.time.sleep")
    @patch("thermaltrend.download_data.yf")
    def test_handles_download_exception(self, mock_yf, mock_sleep, tmp_path):
        mock_yf.download.side_effect = Exception("Network error")

        download_and_save(["FAIL"], "2024-01-01", "2024-01-06", tmp_path)

        assert not (tmp_path / "FAIL.parquet").exists()

    @patch("thermaltrend.download_data.time.sleep")
    @patch("thermaltrend.download_data.yf")
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

    @patch("thermaltrend.download_data.time.sleep")
    @patch("thermaltrend.download_data.yf")
    def test_creates_output_directory(self, mock_yf, mock_sleep, tmp_path, sample_ohlcv):
        mock_yf.download.return_value = sample_ohlcv
        out_dir = tmp_path / "nested" / "data"

        download_and_save(["AAPL"], "2024-01-01", "2024-01-06", out_dir)

        assert out_dir.exists()
        assert (out_dir / "AAPL.parquet").exists()

    @patch("thermaltrend.download_data.time.sleep")
    @patch("thermaltrend.download_data.yf")
    def test_multiple_tickers(self, mock_yf, mock_sleep, tmp_path, sample_ohlcv):
        mock_yf.download.return_value = sample_ohlcv

        download_and_save(["AAPL", "MSFT"], "2024-01-01", "2024-01-06", tmp_path)

        assert (tmp_path / "AAPL.parquet").exists()
        assert (tmp_path / "MSFT.parquet").exists()
        assert mock_yf.download.call_count == 2
