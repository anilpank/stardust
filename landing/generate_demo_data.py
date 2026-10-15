"""Generate ``demo_signals.json`` for the landing-page prototype.

Runs the real stardust engine over a small set of well-known S&P 500 tickers and
records, for each timeframe (1/3/5/10 years), which of the six strategies fit
that stock best — plus a lightweight out-of-sample check — and the current
buy/sell signal from the winning strategy.

This keeps the landing page honest: the numbers are produced by the same engine
the product would use, not invented.

Usage (from the repo root):
    python landing/generate_demo_data.py
    python landing/generate_demo_data.py --tickers AAPL NVDA KO --skip-oos
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from thermaltrend.company_universe import (
    DEFAULT_DATA_DIR,
    analyze_company,
    company_summary_frame,
    recommend_strategy,
    trades_to_frame,
)
from thermaltrend.ticker_search import company_name

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_TICKERS = ["AAPL", "NVDA", "MSFT", "KO", "JPM", "XOM", "WMT", "TSLA"]
WINDOWS = {"1y": 1, "3y": 3, "5y": 5, "10y": 10}
OOS_DEFINITION = "winner re-scored on the most recent 30% of each window (holdout); " \
                 "flagged overfit if the holdout return is negative or win rate < 50%"
OOS_HELDOUT_FRACTION = 0.30
# Trade rows are stored as positional arrays (one entry per column) so the
# committed JSON stays compact; the landing page maps them back by name.
TRADE_COLUMNS = [
    "Entry Date", "Entry Price", "Exit Date", "Exit Price",
    "P&L ($)", "P&L (%)", "Holding (days)", "Exit Reason",
]


def _latest_date(ticker: str, data_dir: Path) -> pd.Timestamp | None:
    path = Path(data_dir) / f"{ticker}.parquet"
    if not path.exists():
        return None
    try:
        idx = pd.read_parquet(path, columns=[]).index
        dates = pd.to_datetime(idx.get_level_values(0)) if isinstance(idx, pd.MultiIndex) else pd.to_datetime(idx)
        return dates.max()
    except Exception:
        return None


def _window_start(end: pd.Timestamp, years: int) -> str:
    return (end - pd.DateOffset(years=years)).strftime("%Y-%m-%d")


def _holdout_start(ticker: str, data_dir: Path, window_start: str) -> str | None:
    path = Path(data_dir) / f"{ticker}.parquet"
    if not path.exists():
        return None
    try:
        idx = pd.read_parquet(path, columns=[]).index
        dates = pd.to_datetime(idx.get_level_values(0)) if isinstance(idx, pd.MultiIndex) else pd.to_datetime(idx)
        dates = dates[dates >= pd.Timestamp(window_start)]
        if len(dates) < 10:
            return None
        split = dates[int(len(dates) * (1 - OOS_HELDOUT_FRACTION))]
        return split.strftime("%Y-%m-%d")
    except Exception:
        return None


def _latest_signal(result: dict) -> dict:
    signals = result.get("signals") or []
    if not signals:
        return {"direction": "HOLD", "strength": 0.0, "as_of": None, "strategy": result.get("strategy_name")}
    last = max(signals, key=lambda s: s.timestamp)
    direction = getattr(last.direction, "value", str(last.direction))
    return {
        "direction": direction,
        "strength": round(float(last.strength), 3),
        "as_of": pd.Timestamp(last.timestamp).strftime("%Y-%m-%d"),
        "strategy": result.get("strategy_name"),
    }


def _metrics(result: dict) -> dict:
    m = result.get("metrics") or {}
    return {
        "win_rate": round(float(m.get("win_rate", 0.0)), 4),
        "total_pnl": round(float(m.get("total_return", 0.0)), 2),
        "cagr": round(float(m.get("cagr", 0.0)), 4),
        "sharpe": round(float(m.get("sharpe", 0.0)), 3),
        "trades_completed": int(m.get("trades_completed", 0)),
    }


def _empty_payload() -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "oos_definition": OOS_DEFINITION,
        "windows": list(WINDOWS.keys()),
        "tickers": {},
    }


def _empty_trades_payload() -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "columns": TRADE_COLUMNS,
        "trades": {},
    }


def _trades(result: dict) -> list[list]:
    """Completed trades for one strategy as positional rows (see TRADE_COLUMNS), newest first."""
    frame = trades_to_frame(result.get("trades") or [])
    if frame.empty:
        return []
    records = sorted(frame.to_dict("records"), key=lambda r: r["Entry Date"], reverse=True)
    return [[rec[col] for col in TRADE_COLUMNS] for rec in records]


def build_ticker(ticker: str, data_dir: Path, skip_oos: bool, min_trades: int
                 ) -> tuple[dict, dict] | None:
    """Compute one ticker's entry and its per-window trades.

    Returns ``(entry, trades)`` where ``trades`` maps a window label to the
    winner strategy's completed trades, or ``None`` when there is no price data.
    """
    end = _latest_date(ticker, data_dir)
    if end is None:
        print(f"  ! {ticker}: no price data, skipping")
        return None

    entry: dict = {
        "name": company_name(ticker) or ticker,
        "signal": {"direction": "HOLD", "strength": 0.0, "as_of": None, "strategy": None},
        "windows": {},
    }
    trades: dict[str, list] = {}
    print(f"  {ticker} ({entry['name']}) — data through {end.date()}")

    for wlabel, years in WINDOWS.items():
        start = _window_start(end, years)
        print(f"    {wlabel}: running six strategies from {start} …")
        results = analyze_company(ticker, start, None, universe="current", data_dir=data_dir)
        summary = company_summary_frame(results)
        rec = recommend_strategy(summary, min_trades=min_trades)
        if not rec:
            entry["windows"][wlabel] = {"strategy": None, "oos": None}
            continue

        winner = rec["strategy"]
        win_result = results.get(winner, {})
        cell = {
            "strategy": winner,
            "low_confidence": bool(rec.get("low_confidence")),
            "oos": None,
        }
        cell.update(_metrics(win_result))
        entry["windows"][wlabel] = cell
        rows = _trades(win_result)
        if rows:
            trades[wlabel] = rows

        if wlabel == "1y":  # shortest window drives the "current" signal
            entry["signal"] = _latest_signal(win_result)

        if skip_oos:
            continue
        holdout = _holdout_start(ticker, data_dir, start)
        if holdout:
            ho = analyze_company(ticker, holdout, None, universe="current",
                                 data_dir=data_dir, strategy_labels=[winner])
            ho_metrics = _metrics(ho.get(winner, {}))
            if ho_metrics["trades_completed"] >= 2:
                cell["oos"] = bool(ho_metrics["total_pnl"] >= 0 and ho_metrics["win_rate"] >= 0.5)

    return entry, trades


def build(tickers: list[str], data_dir: Path, skip_oos: bool, min_trades: int
          ) -> tuple[dict, dict]:
    generated = _empty_payload()
    trades_payload = _empty_trades_payload()
    for ticker in tickers:
        result = build_ticker(ticker, data_dir, skip_oos, min_trades)
        if result is None:
            continue
        entry, trades = result
        generated["tickers"][ticker] = entry
        trades_payload["trades"][ticker] = trades
    return generated, trades_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate landing-page demo signals.")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    parser.add_argument("--output", default=str(BASE_DIR / "demo_signals.json"))
    parser.add_argument("--trades-output", default=str(BASE_DIR / "demo_trades.json"))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--skip-oos", action="store_true", help="Skip the out-of-sample holdout check")
    parser.add_argument("--skip-trades", action="store_true",
                        help="Only write signals, not the trade-level detail file")
    parser.add_argument("--min-trades", type=int, default=3)
    parser.add_argument("--resume", action="store_true",
                        help="Keep tickers already in --output and compute only the rest")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    out = Path(args.output)
    trades_out = Path(args.trades_output)

    if args.resume and out.exists():
        payload = json.loads(out.read_text(encoding="utf-8"))
        payload.setdefault("tickers", {})
        print(f"Resuming — {len(payload['tickers'])} tickers already in {out}")
    else:
        payload = _empty_payload()

    if not args.skip_trades and args.resume and trades_out.exists():
        trades_payload = json.loads(trades_out.read_text(encoding="utf-8"))
        trades_payload.setdefault("trades", {})
        trades_payload.setdefault("columns", TRADE_COLUMNS)
    else:
        trades_payload = _empty_trades_payload()

    print(f"Generating demo data for: {', '.join(args.tickers)}")
    for i, ticker in enumerate(args.tickers, 1):
        if args.resume and ticker in payload["tickers"]:
            continue
        result = build_ticker(ticker, data_dir, args.skip_oos, args.min_trades)
        if result is not None:
            entry, trades = result
            payload["tickers"][ticker] = entry
            if not args.skip_trades:
                trades_payload["trades"][ticker] = trades
        # Checkpoint after every ticker so a long full-universe run is resumable.
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload["generated_at"] = stamp
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if not args.skip_trades:
            trades_payload["generated_at"] = stamp
            trades_out.write_text(json.dumps(trades_payload, separators=(",", ":")), encoding="utf-8")
        print(f"    [{i}/{len(args.tickers)}] {len(payload['tickers'])} tickers written")

    print(f"\nWrote {out}  ({len(payload['tickers'])} tickers)")
    if not args.skip_trades:
        print(f"Wrote {trades_out}  ({len(trades_payload['trades'])} tickers)")


if __name__ == "__main__":
    main()
