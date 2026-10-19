"""Generate the committed shadow-run snapshot for the admin console (Phase B).

Runs the frozen core-only engine as the *model* (what the system would do),
loads the operator's shadow-run journal (fills + decisions), replays the fills
into a *live* ledger marked to market from local Parquet, computes adherence
between the two, and writes one JSON file the console serves. The server never
runs the engine at request time — same pattern as ``generate_portfolio_data.py``.

Modes:
  paper  the live ledger is the model's own path: the model's core trades are
         the paper fills, so no journal is required.
  live   the live ledger is replayed from the operator's journal fills, read
         through the server's guarded export endpoints (or a local dir).

The payload lands in ``landing/private/`` (committed, never served as a static
file — the ``/`` catch-all blocks the ``private/`` prefix).

Usage (from the repo root):
    python landing/generate_portfolio_snapshot.py
    python landing/generate_portfolio_snapshot.py --mode live --base-url http://127.0.0.1:8000
    python landing/generate_portfolio_snapshot.py --journal-dir /tmp/journal
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from thermaltrend.analytics.metrics import RISK_FREE_RATE, TRADING_DAYS_PER_YEAR
from thermaltrend.portfolio.config import PortfolioConfig, load_config
from thermaltrend.portfolio.manager import REASON_DATA_END
from thermaltrend.portfolio_backtest import run_portfolio_backtest

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = BASE_DIR / "private" / "portfolio_snapshot.json"
DEFAULT_DATA_DIR = BASE_DIR.parent / "thermaltrend" / "data" / "equities"
LIVE_CONFIG_PATH = BASE_DIR.parent / "thermaltrend" / "portfolio" / "live_config.json"
DEFAULT_START = "1994-01-03"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
MODE_CHOICES = ("paper", "live")
MODEL_EQUITY_YEARS = 3
EXPOSURE_MATCH_TOLERANCE = 0.05

FILL_FIELDS = [
    "created_at", "trade_date", "ticker", "lane", "side", "shares",
    "price", "source", "proposal_id", "note",
]
DECISION_FIELDS = [
    "created_at", "trade_date", "proposal_id", "action", "ticker", "lane",
    "side", "planned_shares", "planned_price", "note",
]


@dataclass
class Journal:
    fills: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    load_error: str | None = None

    @property
    def source(self) -> str:
        if self.load_error:
            return "error"
        return "loaded" if (self.fills or self.decisions) else "empty"


@dataclass
class LiveLedger:
    equity: pd.Series
    exposure: pd.Series
    positions: list[dict]
    cash: float
    fills_applied: int
    rejected_fills: int
    source: str


def _jsonable(value):
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
    return value


def _iso_dates(index) -> list[str]:
    return [pd.Timestamp(ts).date().isoformat() for ts in index]


def _next_session(day) -> pd.Timestamp:
    nxt = pd.Timestamp(day) + pd.Timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += pd.Timedelta(days=1)
    return nxt


def _series_metrics(equity: pd.Series) -> dict:
    if equity is None or len(equity) < 2:
        value = round(float(equity.iloc[-1]), 2) if equity is not None and len(equity) else None
        return {
            "total_return_pct": 0.0,
            "cagr": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "equity_start": value,
            "equity_end": value,
        }
    start = float(equity.iloc[0])
    end = float(equity.iloc[-1])
    total = end / start - 1.0 if start > 0 else 0.0
    years = len(equity) / TRADING_DAYS_PER_YEAR
    cagr = (end / start) ** (1.0 / years) - 1.0 if start > 0 and years > 0 else 0.0
    returns = equity.pct_change().dropna()
    excess = returns - RISK_FREE_RATE / TRADING_DAYS_PER_YEAR
    sharpe = (
        float(excess.mean() / excess.std() * (TRADING_DAYS_PER_YEAR ** 0.5))
        if len(excess) > 1 and excess.std() > 0
        else 0.0
    )
    drawdown = equity / equity.cummax() - 1.0
    return {
        "total_return_pct": round(total, 6),
        "cagr": round(cagr, 4),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(float(drawdown.min()), 4),
        "equity_start": round(start, 2),
        "equity_end": round(end, 2),
    }


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _http_get_csv(url: str, user: str | None, password: str | None,
                  timeout: float = 10.0) -> list[dict]:
    request = urllib.request.Request(url)
    if user is not None:
        token = base64.b64encode(f"{user}:{password or ''}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8-sig")
    return list(csv.DictReader(text.splitlines()))


def _coerce_fill(row: dict) -> dict:
    return {
        "trade_date": str(row.get("trade_date", "")).strip(),
        "ticker": str(row.get("ticker", "")).strip().upper(),
        "lane": str(row.get("lane", "core")).strip() or "core",
        "side": str(row.get("side", "")).strip().lower(),
        "shares": _int_or_none(row.get("shares")),
        "price": _float_or_none(row.get("price")),
        "source": str(row.get("source", "paper")).strip() or "paper",
        "proposal_id": str(row.get("proposal_id", "")).strip(),
        "note": str(row.get("note", "")).strip(),
    }


def _coerce_decision(row: dict) -> dict:
    return {
        "trade_date": str(row.get("trade_date", "")).strip(),
        "proposal_id": str(row.get("proposal_id", "")).strip(),
        "action": str(row.get("action", "")).strip().lower(),
        "ticker": str(row.get("ticker", "")).strip().upper(),
        "lane": str(row.get("lane", "core")).strip() or "core",
        "side": str(row.get("side", "")).strip().lower(),
        "planned_shares": _int_or_none(row.get("planned_shares")),
        "planned_price": _float_or_none(row.get("planned_price")),
        "note": str(row.get("note", "")).strip(),
    }


def _int_or_none(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_journal(
    base_url: str | None = None,
    journal_dir: str | Path | None = None,
    user: str | None = None,
    password: str | None = None,
    timeout: float = 10.0,
) -> Journal:
    """Load the shadow-run journal from a local dir or the server's export API."""
    fills: list[dict] = []
    decisions: list[dict] = []
    error: str | None = None
    if journal_dir is not None:
        directory = Path(journal_dir)
        fills = _read_csv(directory / "journal_fills.csv")
        decisions = _read_csv(directory / "journal_decisions.csv")
    elif base_url:
        base = base_url.rstrip("/")
        try:
            fills = _http_get_csv(
                f"{base}/api/admin/trade/export/fills.csv", user, password, timeout
            )
            decisions = _http_get_csv(
                f"{base}/api/admin/trade/export/decisions.csv", user, password, timeout
            )
        except Exception as exc:  # network / auth / HTTP errors
            error = f"{type(exc).__name__}: {exc}"
    return Journal(
        fills=[_coerce_fill(r) for r in fills],
        decisions=[_coerce_decision(r) for r in decisions],
        load_error=error,
    )


def load_closes(data_dir: str | Path, tickers) -> dict[str, pd.Series]:
    """Load close prices per ticker from local Parquet (missing tickers skipped)."""
    closes: dict[str, pd.Series] = {}
    for ticker in dict.fromkeys(tickers):
        path = Path(data_dir) / f"{ticker}.parquet"
        if not path.exists():
            continue
        frame = pd.read_parquet(path)
        column = "Close" if "Close" in frame.columns else "close"
        closes[ticker] = pd.Series(frame[column]).dropna().sort_index()
    return closes


def run_model(
    config: PortfolioConfig,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    start: str | None = DEFAULT_START,
    end: str | None = None,
):
    """Run the frozen core-only config as the shadow model."""
    return run_portfolio_backtest(
        strategy_name=None,
        tickers=[],
        start_date=start,
        end_date=end,
        data_dir=str(data_dir),
        universe="current",
        config=config,
        capital=config.capital,
        satellite=False,
    )


def _close_on(closes: pd.Series, date: pd.Timestamp) -> float | None:
    if date not in closes.index:
        return None
    value = closes.loc[date]
    if isinstance(value, pd.Series):
        value = value.iloc[0]
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def replay_fills(
    fills: list[dict],
    config: PortfolioConfig,
    closes: dict[str, pd.Series],
    as_of: pd.Timestamp | None = None,
) -> LiveLedger:
    """Replay executed fills into a cash + positions ledger, marked to market."""
    core = config.core_ticker
    if core not in closes:
        raise ValueError(f"core ticker '{core}' has no price data")
    calendar = closes[core].index
    if as_of is not None:
        calendar = calendar[calendar <= pd.Timestamp(as_of)]

    ordered = sorted(
        fills,
        key=lambda f: (
            pd.Timestamp(f["trade_date"]) if _is_date(f.get("trade_date")) else pd.Timestamp.max,
            str(f.get("created_at", "")),
        ),
    )
    fill_dates = [pd.Timestamp(f["trade_date"]) for f in ordered if _is_date(f.get("trade_date"))]
    if fill_dates:
        calendar = calendar[calendar >= min(fill_dates)]
    else:
        calendar = calendar[:0]

    cash = config.capital
    shares: dict[str, int] = {}
    entry: dict[str, float] = {}
    lane: dict[str, str] = {}
    applied = 0
    rejected = 0
    cursor = 0

    dates: list[pd.Timestamp] = []
    equity_values: list[float] = []
    exposure_values: list[float] = []

    for date in calendar:
        while cursor < len(ordered) and _fill_due(ordered[cursor], date):
            fill = ordered[cursor]
            cursor += 1
            n = fill.get("shares")
            price = fill.get("price")
            ticker = fill.get("ticker")
            if not ticker or n is None or n <= 0 or price is None or price <= 0:
                rejected += 1
                continue
            if fill.get("side") == "buy":
                prior_shares = shares.get(ticker, 0)
                total_cost = entry.get(ticker, 0.0) * prior_shares + price * n
                shares[ticker] = prior_shares + n
                entry[ticker] = total_cost / shares[ticker]
                lane.setdefault(ticker, fill.get("lane", "core"))
                cash -= price * n
                applied += 1
            elif fill.get("side") == "sell":
                held = shares.get(ticker, 0)
                if held <= 0:
                    rejected += 1
                    continue
                sold = min(n, held)
                cash += price * sold
                shares[ticker] = held - sold
                if shares[ticker] == 0:
                    shares.pop(ticker, None)
                    entry.pop(ticker, None)
                    lane.pop(ticker, None)
                applied += 1
            else:
                rejected += 1

        market_value = 0.0
        for ticker, held in shares.items():
            close = _close_on(closes.get(ticker, pd.Series(dtype=float)), date)
            if close is not None:
                market_value += held * close
        equity = cash + market_value
        dates.append(date)
        equity_values.append(equity)
        exposure_values.append(market_value / equity if equity > 0 else 0.0)

    positions = []
    for ticker, held in sorted(shares.items()):
        close = _close_on(closes.get(ticker, pd.Series(dtype=float)), calendar[-1]) if len(calendar) else None
        last = close if close is not None else entry.get(ticker, 0.0)
        positions.append(
            {
                "ticker": ticker,
                "lane": lane.get(ticker, "core"),
                "shares": held,
                "avg_entry": round(entry.get(ticker, 0.0), 4),
                "last_close": round(last, 4),
                "market_value": round(held * last, 2),
                "unrealized_pnl": round((last - entry.get(ticker, 0.0)) * held, 2),
            }
        )

    index = pd.DatetimeIndex(dates)
    return LiveLedger(
        equity=pd.Series(equity_values, index=index, dtype=float),
        exposure=pd.Series(exposure_values, index=index, dtype=float),
        positions=positions,
        cash=round(cash, 2),
        fills_applied=applied,
        rejected_fills=rejected,
        source="journal",
    )


def _is_date(value) -> bool:
    try:
        pd.Timestamp(value)
        return True
    except (TypeError, ValueError):
        return False


def _fill_due(fill: dict, date: pd.Timestamp) -> bool:
    if not _is_date(fill.get("trade_date")):
        return True
    return pd.Timestamp(fill["trade_date"]) <= date


def build_proposal(
    result,
    config: PortfolioConfig,
    ref_price: float | None = None,
    as_of: pd.Timestamp | None = None,
) -> dict | None:
    """The core-lane order the model would send for the next session, if any."""
    if as_of is None:
        as_of = pd.Timestamp(result.daily["date"].iloc[-1])
    pending = [r for r in result.rejected if r.get("reason") == REASON_DATA_END]
    core_orders = [
        r
        for r in pending
        if r.get("lane") == "core" and r.get("ticker") == config.core_ticker
    ]
    if not core_orders:
        return None
    order = core_orders[0]
    history = result.core_history[-1] if result.core_history else {}
    bits = []
    momentum = history.get("momentum")
    if momentum is not None:
        bits.append(f"momentum {momentum * 100:+.1f}%")
    regime = history.get("regime")
    if regime:
        bits.append(f"regime {regime}")
    session = _next_session(as_of)
    return {
        "proposal_id": f"{session.date()}-core-{order['side']}",
        "side": order["side"],
        "ticker": order["ticker"],
        "lane": "core",
        "as_of": as_of.date().isoformat(),
        "for_session": session.date().isoformat(),
        "target_weight": config.core_target_weight,
        "ref_price": round(ref_price, 4) if ref_price is not None else None,
        "reason": ", ".join(bits) or "core signal flip",
    }


def _model_positions(result) -> list[dict]:
    if result.daily is None or len(result.daily) == 0:
        return []
    last = result.daily.iloc[-1]
    core_invested = float(last.get("core_invested", 0.0))
    if core_invested <= 0:
        return []
    return [
        {
            "ticker": result.config.core_ticker,
            "lane": "core",
            "market_value": round(core_invested, 2),
        }
    ]


def _paper_fills(result) -> list[dict]:
    fills = []
    for trade in result.trades:
        ticker = trade.ticker
        lane = "core" if trade.strategy_id == "core" else "satellite"
        fills.append(
            {
                "trade_date": pd.Timestamp(trade.entry_date).date().isoformat(),
                "ticker": ticker,
                "lane": lane,
                "side": "buy",
                "shares": trade.shares,
                "price": trade.entry_price,
                "source": "paper",
                "proposal_id": "",
                "note": "paper fill (model)",
            }
        )
        fills.append(
            {
                "trade_date": pd.Timestamp(trade.exit_date).date().isoformat(),
                "ticker": ticker,
                "lane": lane,
                "side": "sell",
                "shares": trade.shares,
                "price": trade.exit_price,
                "source": "paper",
                "proposal_id": "",
                "note": f"paper fill (model, {trade.exit_reason})",
            }
        )
    return fills


def _trim_equity(result) -> pd.Series:
    equity = result.equity_curve.astype(float)
    if len(equity) == 0:
        return equity
    cutoff = equity.index[-1] - pd.Timedelta(days=int(MODEL_EQUITY_YEARS * 365.25))
    trimmed = equity[equity.index >= cutoff]
    return trimmed if len(trimmed) >= 2 else equity


def _adherence(journal: Journal, model, live: LiveLedger, mode: str) -> dict:
    decisions = journal.decisions
    counts = {"acted": 0, "skipped": 0, "deferred": 0}
    for decision in decisions:
        action = decision.get("action")
        if action in counts:
            counts[action] += 1
    total = sum(counts.values())
    acted_pct = round(counts["acted"] / total, 4) if total else None

    model_exposure = None
    if model.daily is not None and len(model.daily):
        model_exposure = pd.Series(
            model.daily["exposure"].values,
            index=pd.DatetimeIndex(model.daily["date"]),
            dtype=float,
        )
    match_pct = None
    if mode == "paper":
        match_pct = 1.0
    elif len(live.exposure) and model_exposure is not None and len(model_exposure):
        aligned = pd.concat(
            [live.exposure.rename("live"), model_exposure.rename("model")], axis=1
        ).dropna()
        if len(aligned):
            match_pct = round(
                float(
                    (
                        (aligned["live"] - aligned["model"]).abs()
                        <= EXPOSURE_MATCH_TOLERANCE
                    ).mean()
                ),
                4,
            )

    return {
        "fills": len(journal.fills),
        "decisions": len(decisions),
        "acted": counts["acted"],
        "skipped": counts["skipped"],
        "deferred": counts["deferred"],
        "acted_pct": acted_pct,
        "exposure_match_pct": match_pct,
        "rejected_fills": live.rejected_fills,
    }


def build_snapshot(
    config: PortfolioConfig,
    *,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    journal: Journal | None = None,
    mode: str = "paper",
    model_result=None,
    now: datetime | None = None,
) -> dict:
    """Assemble the shadow-run snapshot payload."""
    if mode not in MODE_CHOICES:
        raise ValueError(f"mode must be one of {MODE_CHOICES}")
    journal = journal or Journal()
    if model_result is None:
        model_result = run_model(config, data_dir)

    as_of = pd.Timestamp(model_result.daily["date"].iloc[-1])
    closes = load_closes(
        data_dir,
        [config.core_ticker, *[f["ticker"] for f in journal.fills]],
    )
    ref_price = None
    if config.core_ticker in closes and len(closes[config.core_ticker]):
        core_closes = closes[config.core_ticker]
        ref_price = float(core_closes.asof(as_of))

    model_equity = _trim_equity(model_result)
    model = {
        "equity": {
            "dates": _iso_dates(model_equity.index),
            "values": [round(float(v), 2) for v in model_equity.tolist()],
        },
        "metrics": _jsonable(model_result.metrics),
        "positions": _model_positions(model_result),
    }

    if mode == "paper":
        live = LiveLedger(
            equity=model_equity,
            exposure=pd.Series(
                model_result.daily["exposure"].values,
                index=pd.DatetimeIndex(model_result.daily["date"]),
                dtype=float,
            ),
            positions=_model_positions(model_result),
            cash=round(float(model_result.daily["cash"].iloc[-1]), 2),
            fills_applied=len(model_result.trades),
            rejected_fills=0,
            source="model",
        )
        fills = _paper_fills(model_result)
    else:
        live = replay_fills(journal.fills, config, closes, as_of=as_of)
        fills = journal.fills

    live_payload = {
        "equity": {
            "dates": _iso_dates(live.equity.index),
            "values": [round(float(v), 2) for v in live.equity.tolist()],
        },
        "metrics": _jsonable(_series_metrics(live.equity)),
        "positions": live.positions,
        "cash": live.cash,
        "source": live.source,
    }

    generated = now or datetime.now(timezone.utc)
    return {
        "generated_at": generated.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "as_of": as_of.date().isoformat(),
        "mode": mode,
        "config": _jsonable(config.to_dict()),
        "config_path": str(LIVE_CONFIG_PATH),
        "model": model,
        "live": live_payload,
        "proposal": build_proposal(model_result, config, ref_price=ref_price, as_of=as_of),
        "adherence": _adherence(journal, model_result, live, mode),
        "journal_source": journal.source,
        "journal_load_error": journal.load_error,
        "fills": _jsonable(fills),
    }


def save_snapshot(payload: dict, out_path: str | Path = DEFAULT_OUT) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(
        description="Generate landing/private/portfolio_snapshot.json (shadow run)"
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--config", default=str(LIVE_CONFIG_PATH))
    parser.add_argument("--mode", choices=MODE_CHOICES,
                        default=os.environ.get("PORTFOLIO_MODE", "paper"))
    parser.add_argument("--base-url", default=os.environ.get("LANDING_BASE_URL"))
    parser.add_argument("--journal-dir", default=None)
    parser.add_argument("--user", default=os.environ.get("ADMIN_USER", "admin"))
    parser.add_argument("--password", default=os.environ.get("ADMIN_PASSWORD"))
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=None)
    args = parser.parse_args()

    config = load_config(args.config)

    journal = Journal()
    if args.journal_dir or args.base_url or args.mode == "live":
        journal = load_journal(
            base_url=args.base_url or (DEFAULT_BASE_URL if args.mode == "live" else None),
            journal_dir=args.journal_dir,
            user=args.user,
            password=args.password,
        )

    result = run_model(config, args.data_dir, start=args.start, end=args.end)
    payload = build_snapshot(
        config,
        data_dir=args.data_dir,
        journal=journal,
        mode=args.mode,
        model_result=result,
    )
    path = save_snapshot(payload, args.out)
    size_kb = path.stat().st_size / 1024
    proposal = payload["proposal"]
    action = proposal["side"].upper() if proposal else "HOLD"
    print(
        f"Wrote {path} ({size_kb:,.0f} KB) — mode {payload['mode']}, "
        f"as_of {payload['as_of']}, next: {action}, "
        f"journal {payload['journal_source']}"
    )
    if payload["journal_load_error"]:
        print(f"  warning: journal load error: {payload['journal_load_error']}")


if __name__ == "__main__":
    main()
