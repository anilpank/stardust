"""Generate ``landing/tickers.json`` — the landing page's offline name index.

The landing app is intentionally self-contained: at runtime it never imports
``thermaltrend`` or pandas (the server just serves static files + JSON). This
script, run from the repo root, reads the S&P 500 membership table and writes a
small JSON index of ticker -> company name so the page can turn a typed company
name ("Apple", "Bank of America") into a ticker ("AAPL", "BAC") with no network
call and no database.

Usage (from the repo root):
    python landing/generate_ticker_index.py
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
DEFAULT_MEMBERSHIP_PATH = REPO_ROOT / "thermaltrend" / "data" / "equities" / "membership.csv"

# Tickers that are not current S&P 500 members but should still be searchable.
# SPY is the Dual Momentum benchmark.
EXTRA_NAMES = {
    "SPY": "SPDR S&P 500 ETF Trust",
}

# Brand names that differ from the legal company name, added as extra searchable
# entries so typing the common name still finds the ticker.
BRAND_ALIASES = {
    "META": "Facebook",
    "GOOGL": "Google",
}


def build(membership_path: Path) -> dict:
    """Build the index payload from the point-in-time membership table."""
    frame = pd.read_csv(membership_path)
    current = frame[frame["is_current"].astype(str).str.lower() == "true"]

    entries: list[dict] = []
    for ticker, name in zip(current["ticker"], current["name"]):
        ticker = "" if pd.isna(ticker) else str(ticker).strip()
        if not ticker:
            continue
        name = "" if pd.isna(name) else str(name).strip()
        entries.append({"ticker": ticker, "name": name or ticker})
    for ticker, name in EXTRA_NAMES.items():
        entries.append({"ticker": ticker, "name": name})
    for ticker, brand in BRAND_ALIASES.items():
        entries.append({"ticker": ticker, "name": brand})

    entries.sort(key=lambda e: (e["ticker"], e["name"]))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "thermaltrend/data/equities/membership.csv (is_current)",
        "tickers": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate landing/tickers.json")
    parser.add_argument("--membership", default=str(DEFAULT_MEMBERSHIP_PATH))
    parser.add_argument("--output", default=str(BASE_DIR / "tickers.json"))
    args = parser.parse_args()

    payload = build(Path(args.membership))
    out = Path(args.output)
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {out}  ({len(payload['tickers'])} entries)")


if __name__ == "__main__":
    main()
