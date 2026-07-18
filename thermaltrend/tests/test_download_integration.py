"""Integration tests that hit the network (Yahoo Finance).

These are marked slow so they can be skipped in quick CI runs:
    pytest -m "not slow"
"""

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

TRENNDLEND_DIR = Path(__file__).resolve().parent.parent
DOWNLOAD_SCRIPT = TRENNDLEND_DIR / "download_data.py"


@pytest.mark.slow
class TestDownloadDataIntegration:
    def test_download_single_ticker(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(DOWNLOAD_SCRIPT), "--tickers", "AAPL",
             "--start", "2024-01-01", "--end", "2024-02-01",
             "--output", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr

        parquet_files = list(tmp_path.glob("*.parquet"))
        assert len(parquet_files) == 1

        df = pd.read_parquet(parquet_files[0])
        assert len(df) > 0
        assert all(col in df.columns for col in ["Open", "High", "Low", "Close", "Volume"])

    def test_download_skips_existing(self, tmp_path):
        (tmp_path / "AAPL.parquet").touch()

        result = subprocess.run(
            [sys.executable, str(DOWNLOAD_SCRIPT), "--tickers", "AAPL",
             "--start", "2024-01-01", "--end", "2024-02-01",
             "--output", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0

    def test_download_creates_output_dir(self, tmp_path):
        out = tmp_path / "new_dir"
        result = subprocess.run(
            [sys.executable, str(DOWNLOAD_SCRIPT), "--tickers", "AAPL",
             "--start", "2024-01-01", "--end", "2024-02-01",
             "--output", str(out)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0
        assert out.exists()
        assert len(list(out.glob("*.parquet"))) == 1

    def test_download_multiple_tickers(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(DOWNLOAD_SCRIPT), "--tickers", "AAPL", "MSFT",
             "--start", "2024-01-01", "--end", "2024-02-01",
             "--output", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0

        parquet_files = list(tmp_path.glob("*.parquet"))
        assert len(parquet_files) == 2
