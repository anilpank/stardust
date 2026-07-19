"""Integration tests that use real parquet files on disk.

These are marked slow so they can be skipped in quick CI runs:
    pytest -m "not slow"
"""

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

THERMALTREND_DIR = Path(__file__).resolve().parent.parent
SCRIPT = THERMALTREND_DIR / "show_start_dates.py"


@pytest.fixture
def equities_dir(tmp_path):
    """Create a temporary equities directory with sample parquet files."""
    dates_a = pd.date_range("2010-01-04", periods=100, freq="B")
    df_a = pd.DataFrame({"Close": [100.0] * 100, "Volume": [1_000_000] * 100}, index=dates_a)
    df_a.index.name = "Date"
    df_a.to_parquet(tmp_path / "AAPL.parquet")

    dates_b = pd.date_range("1990-03-26", periods=100, freq="B")
    df_b = pd.DataFrame({"Close": [50.0] * 100, "Volume": [2_000_000] * 100}, index=dates_b)
    df_b.index.name = "Date"
    df_b.to_parquet(tmp_path / "MSFT.parquet")

    return tmp_path


@pytest.mark.slow
class TestShowStartDatesIntegration:
    def test_runs_successfully(self, equities_dir):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(THERMALTREND_DIR),
        )
        assert result.returncode == 0, result.stderr

    def test_output_contains_all_tickers(self, equities_dir, monkeypatch):
        monkeypatch.setattr("show_start_dates.DATA_DIR", equities_dir)

        result = subprocess.run(
            [sys.executable, "-c", f"import sys; sys.path.insert(0, '.'); "
             f"from show_start_dates import DATA_DIR; "
             f"from pathlib import Path; "
             f"import show_start_dates; "
             f"show_start_dates.DATA_DIR = Path('{equities_dir}'); "
             f"show_start_dates.main()"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(THERMALTREND_DIR),
        )
        assert result.returncode == 0, result.stderr
        assert "AAPL" in result.stdout
        assert "MSFT" in result.stdout

    def test_sort_by_start(self, equities_dir):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--sort", "start"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(THERMALTREND_DIR),
        )
        assert result.returncode == 0, result.stderr
        assert "MSFT" in result.stdout
        assert "AAPL" in result.stdout

    def test_sort_by_ticker(self, equities_dir):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--sort", "ticker"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(THERMALTREND_DIR),
        )
        assert result.returncode == 0, result.stderr
        output = result.stdout
        aapl_pos = output.find("AAPL")
        msft_pos = output.find("MSFT")
        assert aapl_pos < msft_pos

    def test_csv_export(self, equities_dir, tmp_path):
        csv_path = tmp_path / "result.csv"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--csv", str(csv_path)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(THERMALTREND_DIR),
        )
        assert result.returncode == 0, result.stderr
        assert csv_path.exists()

        df = pd.read_csv(csv_path)
        assert len(df) == 2
        assert set(df["ticker"].tolist()) == {"AAPL", "MSFT"}

    def test_total_companies_in_output(self, equities_dir):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(THERMALTREND_DIR),
        )
        assert result.returncode == 0, result.stderr
        assert "Total companies: 2" in result.stdout
