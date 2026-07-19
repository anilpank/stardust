from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from show_start_dates import main


@pytest.fixture
def sample_parquet(tmp_path):
    """Create two mock parquet files with different date ranges."""
    dates_a = pd.date_range("2010-01-04", periods=5, freq="B")
    df_a = pd.DataFrame(
        {"Close": [100.0] * 5, "Volume": [1_000_000] * 5},
        index=dates_a,
    )
    df_a.index.name = "Date"
    df_a.to_parquet(tmp_path / "AAPL.parquet")

    dates_b = pd.date_range("1990-03-26", periods=5, freq="B")
    df_b = pd.DataFrame(
        {"Close": [50.0] * 5, "Volume": [2_000_000] * 5},
        index=dates_b,
    )
    df_b.index.name = "Date"
    df_b.to_parquet(tmp_path / "MSFT.parquet")

    return tmp_path


class TestShowStartDates:
    def test_reads_all_parquet_files(self, sample_parquet, capsys):
        with patch("show_start_dates.DATA_DIR", sample_parquet):
            with patch("sys.argv", ["show_start_dates.py"]):
                main()

        output = capsys.readouterr().out
        assert "AAPL" in output
        assert "MSFT" in output
        assert "Total companies: 2" in output

    def test_shows_correct_start_dates(self, sample_parquet, capsys):
        with patch("show_start_dates.DATA_DIR", sample_parquet):
            with patch("sys.argv", ["show_start_dates.py"]):
                main()

        output = capsys.readouterr().out
        assert "2010-01-04" in output
        assert "1990-03-26" in output

    def test_sort_by_start_ascending(self, sample_parquet, capsys):
        with patch("show_start_dates.DATA_DIR", sample_parquet):
            with patch("sys.argv", ["show_start_dates.py", "--sort", "start"]):
                main()

        output = capsys.readouterr().out
        msft_pos = output.find("MSFT")
        aapl_pos = output.find("AAPL")
        assert msft_pos < aapl_pos

    def test_sort_by_ticker(self, sample_parquet, capsys):
        with patch("show_start_dates.DATA_DIR", sample_parquet):
            with patch("sys.argv", ["show_start_dates.py", "--sort", "ticker"]):
                main()

        output = capsys.readouterr().out
        aapl_pos = output.find("AAPL")
        msft_pos = output.find("MSFT")
        assert aapl_pos < msft_pos

    def test_sort_by_rows_descending(self, sample_parquet, capsys):
        with patch("show_start_dates.DATA_DIR", sample_parquet):
            with patch("sys.argv", ["show_start_dates.py", "--sort", "rows"]):
                main()

        output = capsys.readouterr().out
        lines = [l for l in output.split("\n") if l.strip() and "TICKER" not in l and "=" not in l and "Total" not in l and "Earliest" not in l and "Latest" not in l]
        assert len(lines) >= 2

    def test_csv_export(self, sample_parquet, tmp_path):
        csv_path = tmp_path / "out.csv"
        with patch("show_start_dates.DATA_DIR", sample_parquet):
            with patch("sys.argv", ["show_start_dates.py", "--csv", str(csv_path)]):
                main()

        assert csv_path.exists()
        df = pd.read_csv(csv_path)
        assert len(df) == 2
        assert "ticker" in df.columns
        assert "start_date" in df.columns
        assert "end_date" in df.columns
        assert "rows" in df.columns

    def test_csv_content_matches_output(self, sample_parquet, tmp_path, capsys):
        csv_path = tmp_path / "out.csv"
        with patch("show_start_dates.DATA_DIR", sample_parquet):
            with patch("sys.argv", ["show_start_dates.py", "--csv", str(csv_path)]):
                main()

        csv_df = pd.read_csv(csv_path)
        tickers = set(csv_df["ticker"].tolist())
        assert tickers == {"AAPL", "MSFT"}

    def test_handles_corrupt_parquet(self, tmp_path, capsys):
        (tmp_path / "BAD.parquet").write_bytes(b"not a parquet file")

        good_dates = pd.date_range("2020-06-01", periods=3, freq="B")
        good_df = pd.DataFrame({"Close": [2.0] * 3}, index=good_dates)
        good_df.index.name = "Date"
        good_df.to_parquet(tmp_path / "GOOD.parquet")

        with patch("show_start_dates.DATA_DIR", tmp_path):
            with patch("sys.argv", ["show_start_dates.py"]):
                main()

        output = capsys.readouterr().out
        assert "GOOD" in output
        assert "Total companies: 2" in output

    def test_empty_directory(self, tmp_path, capsys):
        with patch("show_start_dates.DATA_DIR", tmp_path):
            with patch("sys.argv", ["show_start_dates.py"]):
                main()

        output = capsys.readouterr().out
        assert "No parquet files found." in output

    def test_single_file(self, tmp_path, capsys):
        dates = pd.date_range("2022-01-03", periods=5, freq="B")
        df = pd.DataFrame({"Close": [10.0] * 5}, index=dates)
        df.index.name = "Date"
        df.to_parquet(tmp_path / "TSLA.parquet")

        with patch("show_start_dates.DATA_DIR", tmp_path):
            with patch("sys.argv", ["show_start_dates.py"]):
                main()

        output = capsys.readouterr().out
        assert "TSLA" in output
        assert "2022-01-03" in output
        assert "Total companies: 1" in output

    def test_header_and_summary_printed(self, sample_parquet, capsys):
        with patch("show_start_dates.DATA_DIR", sample_parquet):
            with patch("sys.argv", ["show_start_dates.py"]):
                main()

        output = capsys.readouterr().out
        assert "TICKER" in output
        assert "START DATE" in output
        assert "END DATE" in output
        assert "ROWS" in output
        assert "Earliest data:" in output
        assert "Latest start:" in output
