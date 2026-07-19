"""Integration tests that hit the network (Yahoo Finance).

These are marked slow so they can be skipped in quick CI runs:
    pytest -m "not slow"
"""

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

THERMALTREND_DIR = Path(__file__).resolve().parent.parent
DOWNLOAD_SCRIPT = THERMALTREND_DIR / "download_data.py"
UPDATE_SCRIPT = THERMALTREND_DIR / "update_data.py"


def _download_ticker(tmp_path: Path, ticker: str, start: str, end: str) -> None:
    """Helper: download a ticker to tmp_path using download_data.py."""
    result = subprocess.run(
        [sys.executable, str(DOWNLOAD_SCRIPT), "--tickers", ticker,
         "--start", start, "--end", end, "--output", str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.slow
class TestUpdateIntegration:
    def test_update_single_ticker(self, tmp_path):
        _download_ticker(tmp_path, "AAPL", "2024-01-01", "2024-02-01")

        before = pd.read_parquet(tmp_path / "AAPL.parquet")
        before_count = len(before)

        result = subprocess.run(
            [sys.executable, str(UPDATE_SCRIPT), "--tickers", "AAPL",
             "--output", str(tmp_path)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr

        after = pd.read_parquet(tmp_path / "AAPL.parquet")
        assert len(after) >= before_count
        assert after.index.max() >= before.index.max()

    def test_update_no_existing_files(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(UPDATE_SCRIPT), "--output", str(tmp_path)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "No parquet files found" in result.stdout

    def test_update_specific_tickers(self, tmp_path):
        _download_ticker(tmp_path, "AAPL", "2024-01-01", "2024-02-01")
        _download_ticker(tmp_path, "MSFT", "2024-01-01", "2024-02-01")

        aapl_before = pd.read_parquet(tmp_path / "AAPL.parquet")

        result = subprocess.run(
            [sys.executable, str(UPDATE_SCRIPT), "--tickers", "AAPL",
             "--output", str(tmp_path)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr

        aapl_after = pd.read_parquet(tmp_path / "AAPL.parquet")
        msft_after = pd.read_parquet(tmp_path / "MSFT.parquet")

        assert len(aapl_after) >= len(aapl_before)
        assert len(msft_after) == len(aapl_before)  # MSFT unchanged

    def test_update_idempotent(self, tmp_path):
        _download_ticker(tmp_path, "AAPL", "2024-01-01", "2024-02-01")

        result1 = subprocess.run(
            [sys.executable, str(UPDATE_SCRIPT), "--tickers", "AAPL",
             "--output", str(tmp_path)],
            capture_output=True, text=True, timeout=60,
        )
        assert result1.returncode == 0

        first = pd.read_parquet(tmp_path / "AAPL.parquet")

        result2 = subprocess.run(
            [sys.executable, str(UPDATE_SCRIPT), "--tickers", "AAPL",
             "--output", str(tmp_path)],
            capture_output=True, text=True, timeout=60,
        )
        assert result2.returncode == 0

        second = pd.read_parquet(tmp_path / "AAPL.parquet")
        assert len(second) == len(first)
