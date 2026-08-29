"""Integration tests for download_removed.py that hit the network (Yahoo).

These are marked slow so they can be skipped in quick CI runs:
    pytest -m "not slow"

They write into a temp dir via --output; the real data/equities_removed/
directory is never touched.
"""

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

TRENNDLEND_DIR = Path(__file__).resolve().parent.parent
REMOVED_SCRIPT = TRENNDLEND_DIR / "download_removed.py"


@pytest.mark.slow
class TestDownloadRemovedIntegration:
    def _run(self, tmp_path, *tickers):
        return subprocess.run(
            [sys.executable, str(REMOVED_SCRIPT), "--tickers", *tickers,
             "--output", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=90,
        )

    def test_downloads_known_removed_ticker(self, tmp_path):
        # AAL left the S&P 500 in 2015 but still trades -> Yahoo serves it.
        result = self._run(tmp_path, "AAL")

        assert result.returncode == 0, result.stderr

        parquets = list(tmp_path.glob("*.parquet"))
        assert len(parquets) == 1

        df = pd.read_parquet(parquets[0])
        assert len(df) > 0
        assert all(col in df.columns for col in ["Open", "High", "Low", "Close", "Volume"])

    def test_skips_existing_ticker(self, tmp_path):
        (tmp_path / "AAL.parquet").touch()

        result = self._run(tmp_path, "AAL")

        assert result.returncode == 0, result.stderr

        parquets = list(tmp_path.glob("*.parquet"))
        assert len(parquets) == 1
        assert parquets[0].stat().st_size == 0  # untouched by the downloader

    def test_genuinely_delisted_ticker_writes_nothing(self, tmp_path):
        # ENRNQ (Enron) was delisted; Yahoo returns no series.
        result = self._run(tmp_path, "ENRNQ")

        assert result.returncode == 0, result.stderr
        assert list(tmp_path.glob("*.parquet")) == []

    def test_mixed_tickers_saves_only_recoverable(self, tmp_path):
        result = self._run(tmp_path, "AAL", "ENRNQ")

        assert result.returncode == 0, result.stderr

        saved = {p.stem for p in tmp_path.glob("*.parquet")}
        assert saved == {"AAL"}