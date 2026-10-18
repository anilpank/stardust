"""Generate the committed portfolio-backtest payload for the admin console.

Runs the portfolio engine (``thermaltrend.portfolio_backtest``) offline over a
small set of configured runs and writes one JSON file that ``server.py`` serves
behind admin Basic auth at ``/api/admin/portfolio``. The server never runs the
engine at request time — same pattern as ``generate_demo_data.py``.

The payload lands in ``landing/private/`` (committed, never served as a static
file — the ``/`` catch-all blocks the ``private/`` prefix).

Usage (from the repo root):
    python landing/generate_portfolio_data.py
    python landing/generate_portfolio_data.py --out /tmp/pf.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from thermaltrend.portfolio.config import PortfolioConfig
from thermaltrend.portfolio_backtest import run_portfolio_backtest

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = BASE_DIR / "private" / "portfolio_data.json"
DEFAULT_DATA_DIR = BASE_DIR.parent / "thermaltrend" / "data" / "equities"

DEFAULT_RUNS = [
    {
        "id": "ma-crossover-aapl-msft-10k",
        "label": "MA crossover · AAPL + MSFT · $10K",
        "strategy": "ma_crossover",
        "tickers": ["AAPL", "MSFT"],
        "capital": 10_000.0,
    },
    {
        "id": "donchian-aapl-msft-nvda-jpm-10k",
        "label": "Donchian · AAPL MSFT NVDA JPM · $10K",
        "strategy": "donchian",
        "tickers": ["AAPL", "MSFT", "NVDA", "JPM"],
        "capital": 10_000.0,
    },
    {
        "id": "ma-crossover-aapl-msft-20k",
        "label": "MA crossover · AAPL + MSFT · $20K",
        "strategy": "ma_crossover",
        "tickers": ["AAPL", "MSFT"],
        "capital": 20_000.0,
    },
    # Controls that decompose where the return comes from: the same $10K and
    # the same risk overlay, once with the satellite lane switched off and once
    # with the core sleeve switched off. Pinned to a shared start (SPY history
    # plus regime/momentum warm-up) so the two controls are comparable.
    {
        "id": "core-only-spy-10k",
        "label": "Control: SPY core only · no satellite · $10K",
        "satellite": False,
        "capital": 10_000.0,
        "start": "1994-01-03",
    },
    {
        "id": "satellite-only-aapl-msft-10k",
        "label": "Control: satellite only · no core · $10K",
        "strategy": "ma_crossover",
        "tickers": ["AAPL", "MSFT"],
        "capital": 10_000.0,
        "start": "1994-01-03",
        "config": replace(PortfolioConfig(), core_enabled=False),
    },
]

_REGIME_CODES = {"bull": "b", "bear": "r", "sideways": "s"}


def _jsonable(value):
    """Recursively coerce numpy scalars, datetimes, enums and dataclasses."""
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and pd.isna(value):
        return None
    if hasattr(value, "item") and hasattr(value, "dtype"):
        return _jsonable(value.item())
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat(timespec="seconds")
    if hasattr(value, "value") and hasattr(value, "name"):
        return value.value
    return value


def _iso_dates(index) -> list[str]:
    return [pd.Timestamp(ts).date().isoformat() for ts in index]


def _load_core_closes(data_dir: str | Path, ticker: str) -> pd.Series | None:
    path = Path(data_dir) / f"{ticker}.parquet"
    if not path.exists():
        return None
    frame = pd.read_parquet(path)
    closes = frame["Close"] if "Close" in frame.columns else frame["close"]
    return pd.Series(closes).dropna().sort_index()


def _serialize_run(spec: dict, result, core_closes: pd.Series | None) -> dict:
    cfg = result.config
    equity = result.equity_curve.astype(float)
    daily = result.daily

    payload = {
        "id": spec["id"],
        "label": spec["label"],
        "strategy": spec.get("strategy"),
        "tickers": list(spec.get("tickers") or []),
        "capital": cfg.capital,
        "config": _jsonable(cfg.to_dict()),
        "metrics": _jsonable(result.metrics),
        "equity": {
            "dates": _iso_dates(equity.index),
            "portfolio": [round(float(v), 2) for v in equity.tolist()],
        },
        "daily": {
            "dates": _iso_dates(pd.DatetimeIndex(daily["date"])) if len(daily) else [],
            "exposure": [round(float(v), 4) for v in daily["exposure"].tolist()],
            "breaker": [1 if v else 0 for v in daily["breaker"].tolist()],
            "core": [1 if v > 0 else 0 for v in daily["core_invested"].tolist()],
            "regime": [
                _REGIME_CODES.get(v, "") if isinstance(v, str) else ""
                for v in daily["regime"].tolist()
            ],
        },
        "trades": [
            {**_jsonable(asdict(t)), "lane": "core" if t.strategy_id == "core" else "satellite"}
            for t in result.trades
        ],
        "ratchet_events": _jsonable(result.ratchet_events),
        "breaker_events": _jsonable(result.breaker_events),
        "rejected_counts": _rejected_counts(result.rejected),
    }

    if core_closes is not None and len(core_closes):
        aligned = core_closes.reindex(equity.index, method="ffill")
        valid = aligned.dropna()
        if len(valid):
            scale = cfg.capital / float(valid.iloc[0])
            payload["equity"]["spy"] = [
                round(float(v) * scale, 2) if pd.notna(v) else None
                for v in aligned.tolist()
            ]
    return payload


def _rejected_counts(rejected: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in rejected:
        reason = item.get("reason", "?")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def build_payload(
    runs: list[dict] | None = None,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    config: PortfolioConfig | None = None,
) -> dict:
    """Run every configured backtest and return the JSON-ready payload."""
    specs = runs if runs is not None else DEFAULT_RUNS
    core_cache: dict[str, pd.Series | None] = {}
    payload_runs = []
    for spec in specs:
        run_config = spec.get("config", config)
        core_ticker = (run_config or PortfolioConfig()).core_ticker
        if core_ticker not in core_cache:
            core_cache[core_ticker] = _load_core_closes(data_dir, core_ticker)
        result = run_portfolio_backtest(
            strategy_name=spec.get("strategy"),
            tickers=list(spec.get("tickers") or []),
            start_date=spec.get("start"),
            end_date=spec.get("end"),
            params=spec.get("params"),
            data_dir=str(data_dir),
            config=run_config,
            capital=spec.get("capital"),
            satellite=spec.get("satellite", True),
        )
        payload_runs.append(_serialize_run(spec, result, core_cache[core_ticker]))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runs": payload_runs,
    }


def save_payload(payload: dict, out_path: str | Path = DEFAULT_OUT) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(
        description="Generate landing/private/portfolio_data.json for the admin console"
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    args = parser.parse_args()

    payload = build_payload(data_dir=args.data_dir)
    path = save_payload(payload, args.out)
    size_kb = path.stat().st_size / 1024
    print(f"Wrote {path} ({size_kb:,.0f} KB, {len(payload['runs'])} runs)")


if __name__ == "__main__":
    main()
