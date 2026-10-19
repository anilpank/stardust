"""Minimal backend for the stardust signals landing-page experiment.

Serves the static landing page and records the two things the experiment
actually measures:

* ``POST /api/signup`` — a visitor traded their email for a ticker's signal
  (the primary conversion event).
* ``POST /api/track``  — a funnel/behaviour event (visit, ticker_lookup,
  signal_view, cta_click, return_visit, willingness-to-pay clicks...).

It also serves two read-only endpoints off the pre-generated demo data:

* ``GET  /api/trades`` — the trade-by-trade detail behind a window's winning
  strategy (read from ``demo_trades.json``, cached in memory).
* ``GET  /api/quote``  — a delayed live quote for one ticker, proxied from
  Yahoo Finance and cached briefly. The browser can't call Yahoo directly
  (CORS), and proxying keeps the API keyless and the visitor's IP off Yahoo.

The signup and track events are written to a single SQLite database
(``landing.db``) under ``LANDING_DATA_DIR`` — the only "backend" a short
experiment needs. The admin
console reads the same DB; leads and events can be exported to CSV, and the
whole database downloaded as a consistent snapshot. No third-party analytics
required; drop in PostHog/Plausible in ``app.js`` if you want more.

Run locally:
    python landing/server.py            # http://127.0.0.1:8000

Run in production (AWS):
    gunicorn --bind 0.0.0.0:8000 --chdir landing server:app
"""

from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("LANDING_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "landing.db"
# Legacy CSV output from earlier builds — read once to migrate, never written.
SIGNUPS_CSV = DATA_DIR / "signups.csv"
EVENTS_CSV = DATA_DIR / "events.csv"
DEMO_JSON = BASE_DIR / "demo_signals.json"
TRADES_JSON = BASE_DIR / "demo_trades.json"
PORTFOLIO_JSON = BASE_DIR / "private" / "portfolio_data.json"

# Delayed quotes for the prototype, proxied from Yahoo Finance. Results are
# cached so a burst of lookups on the same ticker hits Yahoo once; failures are
# cached too (briefly) so a bad symbol can't hammer the upstream API.
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
QUOTE_TTL = 60          # seconds a good quote is reused
QUOTE_FAIL_TTL = 30     # seconds a failed lookup is remembered
QUOTE_TIMEOUT = 6       # seconds to wait on Yahoo
QUOTE_USER_AGENT = "Mozilla/5.0 (compatible; stardust-prototype)"

# HTTP Basic auth for the /admin console. Set a password before deploying:
#     ADMIN_PASSWORD=...         (local dev; plaintext)
#     ADMIN_PASS_HASH=salt:hash  (production; generate with gen_admin_password.py)
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_PASS_HASH = os.environ.get("ADMIN_PASS_HASH", "")
AUTH_FAIL_LIMIT = 6
AUTH_FAIL_WINDOW = 5 * 60  # seconds

DATA_DIR.mkdir(parents=True, exist_ok=True)

SIGNUP_FIELDS = [
    "timestamp", "email", "ticker", "source", "utm_medium", "utm_campaign",
    "referrer", "visitor_id", "is_return", "user_agent", "ip",
]
EVENT_FIELDS = [
    "timestamp", "event", "ticker", "source", "visitor_id", "is_return",
    "path", "referrer", "meta",
]

# The portfolio shadow-run journal (Phase B): append-only records of the
# operator's decisions on each proposal and the fills that actually happened.
# The server is the single writer; the offline snapshot generator reads these
# back through the guarded export endpoints.
JOURNAL_FILL_FIELDS = [
    "created_at", "trade_date", "ticker", "lane", "side", "shares",
    "price", "source", "proposal_id", "note",
]
JOURNAL_DECISION_FIELDS = [
    "created_at", "trade_date", "proposal_id", "action", "ticker", "lane",
    "side", "planned_shares", "planned_price", "note",
]
SIDE_CHOICES = ("buy", "sell")
LANE_CHOICES = ("core", "satellite")
SOURCE_CHOICES = ("paper", "live")
ACTION_CHOICES = ("acted", "skipped", "deferred")

# Columns are stored as TEXT; rows are returned as plain dicts so the summary,
# CSV export, and JSON API all share one shape.
SCHEMA = """
CREATE TABLE IF NOT EXISTS signups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    email TEXT NOT NULL,
    ticker TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'direct',
    utm_medium TEXT NOT NULL DEFAULT '',
    utm_campaign TEXT NOT NULL DEFAULT '',
    referrer TEXT NOT NULL DEFAULT '',
    visitor_id TEXT NOT NULL DEFAULT '',
    is_return TEXT NOT NULL DEFAULT 'false',
    user_agent TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    event TEXT NOT NULL,
    ticker TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'direct',
    visitor_id TEXT NOT NULL DEFAULT '',
    is_return TEXT NOT NULL DEFAULT 'false',
    path TEXT NOT NULL DEFAULT '',
    referrer TEXT NOT NULL DEFAULT '',
    meta TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS journal_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    trade_date TEXT NOT NULL DEFAULT '',
    ticker TEXT NOT NULL DEFAULT '',
    lane TEXT NOT NULL DEFAULT 'core',
    side TEXT NOT NULL DEFAULT '',
    shares INTEGER NOT NULL DEFAULT 0,
    price REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'paper',
    proposal_id TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS journal_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    trade_date TEXT NOT NULL DEFAULT '',
    proposal_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    ticker TEXT NOT NULL DEFAULT '',
    lane TEXT NOT NULL DEFAULT 'core',
    side TEXT NOT NULL DEFAULT '',
    planned_shares INTEGER NOT NULL DEFAULT 0,
    planned_price REAL NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_signups_ticker ON signups(ticker);
CREATE INDEX IF NOT EXISTS idx_events_event ON events(event);
CREATE INDEX IF NOT EXISTS idx_events_ticker ON events(ticker);
CREATE INDEX IF NOT EXISTS idx_journal_fills_ticker ON journal_fills(ticker);
CREATE INDEX IF NOT EXISTS idx_journal_fills_date ON journal_fills(trade_date);
CREATE INDEX IF NOT EXISTS idx_journal_decisions_proposal ON journal_decisions(proposal_id);
"""

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TICKER_RE = re.compile(r"^[A-Z.\-]{1,6}$")
_init_lock = threading.Lock()
_initialized: set[str] = set()
_trades_lock = threading.Lock()
_trades_cache: dict = {"mtime": None, "payload": None}
_quote_lock = threading.Lock()
_quote_cache: dict[str, tuple[float, dict]] = {}  # ticker -> (expires_at, quote)
_quote_fail_cache: dict[str, float] = {}          # ticker -> retry-after timestamp
_auth_lock = threading.Lock()
_auth_failures: dict[str, list[float]] = {}

app = Flask(__name__, static_folder=None)
try:  # honour X-Forwarded-* when running behind nginx / a load balancer
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
except Exception:  # pragma: no cover - werkzeug always ships ProxyFix
    pass


def _migrate_legacy(conn: sqlite3.Connection) -> None:
    """Import any pre-SQLite CSV output once, if the tables are still empty."""
    for path, table, fields in (
        (SIGNUPS_CSV, "signups", SIGNUP_FIELDS),
        (EVENTS_CSV, "events", EVENT_FIELDS),
    ):
        if not path.exists():
            continue
        if conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
            continue
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            continue
        cols = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        conn.executemany(
            f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
            [tuple(r.get(f) or "" for f in fields) for r in rows],
        )


def _open() -> sqlite3.Connection:
    """Return a ready connection; create the schema (once per process) on first use."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = str(DB_PATH)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    if path not in _initialized:
        with _init_lock:
            if path not in _initialized:
                conn.executescript(SCHEMA)
                _migrate_legacy(conn)
                conn.commit()
                _initialized.add(path)
    return conn


def _insert_row(table: str, fields: list[str], row: dict) -> int:
    """Insert one row and return its ``rowid``."""
    conn = _open()
    try:
        cols = ", ".join(fields)
        placeholders = ", ".join(":" + f for f in fields)
        with conn:
            cur = conn.execute(
                f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", row
            )
            return cur.lastrowid
    finally:
        conn.close()


def _insert(table: str, fields: list[str], row: dict) -> None:
    _insert_row(table, fields, row)


def _read(table: str, fields: list[str]) -> list[dict]:
    conn = _open()
    try:
        cols = "id, " + ", ".join(fields)
        rows = conn.execute(f"SELECT {cols} FROM {table} ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


def _payload() -> dict:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return {k: v for k, v in request.form.items()}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bool(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "true" if str(value).strip().lower() in {"1", "true", "yes"} else "false"


def _parse_trade_date(value) -> str:
    """Normalise an execution date to YYYY-MM-DD; default to today (UTC).

    Returns "" for a malformed date so callers can reject it.
    """
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc).date().isoformat()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return ""


def _int_field(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_field(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fill_from_payload(data: dict) -> tuple[dict | None, str | None]:
    """Validate and normalise a posted fill into a journal row."""
    ticker = str(data.get("ticker", "")).strip().upper()
    if not _TICKER_RE.match(ticker):
        return None, "ticker is required and must look like a symbol"
    side = str(data.get("side", "")).strip().lower()
    if side not in SIDE_CHOICES:
        return None, "side must be 'buy' or 'sell'"
    shares = _int_field(data.get("shares"))
    if shares is None or shares <= 0:
        return None, "shares must be a positive integer"
    price = _float_field(data.get("price"))
    if price is None or price <= 0:
        return None, "price must be a positive number"
    trade_date = _parse_trade_date(data.get("trade_date"))
    if not trade_date:
        return None, "trade_date must be YYYY-MM-DD"
    lane = str(data.get("lane", "core")).strip().lower() or "core"
    if lane not in LANE_CHOICES:
        return None, "lane must be 'core' or 'satellite'"
    source = str(data.get("source", "paper")).strip().lower() or "paper"
    if source not in SOURCE_CHOICES:
        return None, "source must be 'paper' or 'live'"
    return {
        "created_at": _now(),
        "trade_date": trade_date,
        "ticker": ticker,
        "lane": lane,
        "side": side,
        "shares": shares,
        "price": price,
        "source": source,
        "proposal_id": str(data.get("proposal_id", "")).strip(),
        "note": str(data.get("note", "")).strip(),
    }, None


def _decision_from_payload(data: dict) -> tuple[dict | None, str | None]:
    """Validate and normalise a posted decision into a journal row."""
    action = str(data.get("action", "")).strip().lower()
    if action not in ACTION_CHOICES:
        return None, "action must be 'acted', 'skipped' or 'deferred'"
    ticker = str(data.get("ticker", "")).strip().upper()
    if not _TICKER_RE.match(ticker):
        return None, "ticker is required and must look like a symbol"
    side = str(data.get("side", "")).strip().lower()
    if side not in SIDE_CHOICES:
        return None, "side must be 'buy' or 'sell'"
    trade_date = _parse_trade_date(data.get("trade_date"))
    if not trade_date:
        return None, "trade_date must be YYYY-MM-DD"
    planned_shares = _int_field(data.get("planned_shares", 0))
    if planned_shares is None or planned_shares < 0:
        return None, "planned_shares must be a non-negative integer"
    planned_price = _float_field(data.get("planned_price", 0))
    if planned_price is None or planned_price < 0:
        return None, "planned_price must be a non-negative number"
    lane = str(data.get("lane", "core")).strip().lower() or "core"
    if lane not in LANE_CHOICES:
        return None, "lane must be 'core' or 'satellite'"
    return {
        "created_at": _now(),
        "trade_date": trade_date,
        "proposal_id": str(data.get("proposal_id", "")).strip(),
        "action": action,
        "ticker": ticker,
        "lane": lane,
        "side": side,
        "planned_shares": planned_shares,
        "planned_price": planned_price,
        "note": str(data.get("note", "")).strip(),
    }, None


def _to_csv(fields: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return "\ufeff" + buf.getvalue()  # BOM so Excel opens UTF-8 correctly


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "time": _now()})


@app.post("/api/signup")
def signup():
    data = _payload()
    email = str(data.get("email", "")).strip().lower()
    ticker = str(data.get("ticker", "")).strip().upper()

    if not _EMAIL_RE.match(email):
        return jsonify({"ok": False, "error": "Please enter a valid email."}), 400
    if ticker and not _TICKER_RE.match(ticker):
        return jsonify({"ok": False, "error": "Please enter a valid ticker."}), 400

    _insert(
        "signups",
        SIGNUP_FIELDS,
        {
            "timestamp": _now(),
            "email": email,
            "ticker": ticker,
            "source": data.get("source", "direct"),
            "utm_medium": data.get("utm_medium", ""),
            "utm_campaign": data.get("utm_campaign", ""),
            "referrer": data.get("referrer", ""),
            "visitor_id": data.get("visitor_id", ""),
            "is_return": _bool(data.get("is_return", False)),
            "user_agent": request.headers.get("User-Agent", ""),
            "ip": _client_ip(),
        },
    )
    return jsonify({"ok": True, "email": email, "ticker": ticker})


@app.post("/api/track")
def track():
    data = _payload()
    event = str(data.get("event", "")).strip()
    if not event:
        return jsonify({"ok": False, "error": "event is required"}), 400

    meta = data.get("meta")
    _insert(
        "events",
        EVENT_FIELDS,
        {
            "timestamp": _now(),
            "event": event,
            "ticker": str(data.get("ticker", "")).strip().upper(),
            "source": data.get("source", "direct"),
            "visitor_id": data.get("visitor_id", ""),
            "is_return": _bool(data.get("is_return", False)),
            "path": data.get("path", ""),
            "referrer": data.get("referrer", ""),
            "meta": json.dumps(meta, separators=(",", ":")) if meta else "",
        },
    )
    return jsonify({"ok": True})


@app.get("/api/demo")
def demo():
    if not DEMO_JSON.exists():
        return jsonify({"available": False, "ticker": None, "data": None})
    with DEMO_JSON.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    ticker = request.args.get("ticker", "").strip().upper()
    tickers = payload.get("tickers", {})
    return jsonify(
        {
            "available": ticker in tickers,
            "ticker": ticker,
            "generated_at": payload.get("generated_at"),
            "oos_definition": payload.get("oos_definition"),
            "data": tickers.get(ticker),
            "known": sorted(tickers.keys()),
        }
    )


def _load_trades() -> dict | None:
    """Parse demo_trades.json once and cache it until the file changes."""
    if not TRADES_JSON.exists():
        return None
    try:
        mtime = TRADES_JSON.stat().st_mtime
    except OSError:
        return None
    with _trades_lock:
        if _trades_cache["mtime"] == mtime and _trades_cache["payload"] is not None:
            return _trades_cache["payload"]
        try:
            payload = json.loads(TRADES_JSON.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        _trades_cache.update(mtime=mtime, payload=payload)
        return payload


@app.get("/api/trades")
def trades():
    """Trade-level drill-down for one ticker (pre-generated, cached in memory)."""
    ticker = request.args.get("ticker", "").strip().upper()
    payload = _load_trades()
    if payload is None:
        return jsonify({"available": False, "ticker": ticker, "columns": [], "windows": {}})
    entries = payload.get("trades", {})
    return jsonify(
        {
            "available": ticker in entries,
            "ticker": ticker,
            "generated_at": payload.get("generated_at"),
            "columns": payload.get("columns", []),
            "windows": entries.get(ticker, {}),
        }
    )


# --------------------------------------------------------------------------- #
# Live quote — a thin, cached proxy for the prototype's "current price" chip.
# --------------------------------------------------------------------------- #
def _fetch_quote(ticker: str) -> dict | None:
    """Fetch one ticker's latest price from Yahoo Finance (None on any failure).

    Returns a small, JSON-safe dict. Never raises: the landing page degrades to
    hiding the chip when a quote is unavailable.
    """
    url = YAHOO_CHART_URL.format(ticker=urllib.parse.quote(ticker)) + "?range=1d&interval=1m"
    request_ = urllib.request.Request(url, headers={"User-Agent": QUOTE_USER_AGENT})
    try:
        with urllib.request.urlopen(request_, timeout=QUOTE_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None

    results = (payload.get("chart") or {}).get("result")
    if not results:
        return None
    meta = results[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    if not isinstance(price, (int, float)):
        return None
    previous = meta.get("chartPreviousClose")
    if not isinstance(previous, (int, float)):
        previous = meta.get("previousClose")
    change = round(price - previous, 4) if isinstance(previous, (int, float)) else None
    change_pct = round(change / previous, 6) if change is not None and previous else None
    market_time = meta.get("regularMarketTime")
    as_of = (
        datetime.fromtimestamp(market_time, tz=timezone.utc).isoformat(timespec="seconds")
        if isinstance(market_time, (int, float)) else None
    )
    return {
        "ticker": meta.get("symbol") or ticker,
        "price": price,
        "previous_close": previous,
        "change": change,
        "change_pct": change_pct,
        "currency": meta.get("currency"),
        "exchange": meta.get("exchangeName"),
        "as_of": as_of,
    }


def _cached_quote(ticker: str):
    """Return (quote, error). Good and failed results are both cached briefly."""
    now = time.time()
    with _quote_lock:
        entry = _quote_cache.get(ticker)
        if entry and entry[0] > now:
            return entry[1], None
        retry_after = _quote_fail_cache.get(ticker)
        if retry_after and retry_after > now:
            return None, "unavailable"

    quote = _fetch_quote(ticker)
    with _quote_lock:
        if quote is None:
            _quote_fail_cache[ticker] = now + QUOTE_FAIL_TTL
            return None, "unavailable"
        _quote_cache[ticker] = (now + QUOTE_TTL, quote)
        _quote_fail_cache.pop(ticker, None)
    return quote, None


@app.get("/api/quote")
def quote():
    ticker = request.args.get("ticker", "").strip().upper()
    if not _TICKER_RE.match(ticker):
        return jsonify({"ok": False, "error": "Please enter a valid ticker."}), 400
    result, error = _cached_quote(ticker)
    if error:
        return jsonify({"ok": False, "error": error, "ticker": ticker}), 502
    return jsonify({"ok": True, "ticker": ticker, "quote": result})


# --------------------------------------------------------------------------- #
# Admin view — read-only dashboard over the recorded data.
# --------------------------------------------------------------------------- #
def _verify_admin_password(password: str) -> bool:
    """Check a password against ADMIN_PASS_HASH (scrypt) or ADMIN_PASSWORD (plain)."""
    if ADMIN_PASS_HASH:
        salt, _, expected_hex = ADMIN_PASS_HASH.partition(":")
        if not salt or not expected_hex:
            return False
        try:
            expected = bytes.fromhex(expected_hex)
        except ValueError:
            return False
        derived = hashlib.scrypt(
            password.encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=len(expected)
        )
        return hmac.compare_digest(derived, expected)
    if ADMIN_PASSWORD:
        return hmac.compare_digest(password, ADMIN_PASSWORD)
    return False


def _basic_credentials():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return None
    try:
        raw = base64.b64decode(header[6:].strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    user, sep, password = raw.partition(":")
    return (user, password) if sep else None


def _auth_blocked(ip: str) -> bool:
    now = time.monotonic()
    with _auth_lock:
        recent = [t for t in _auth_failures.get(ip, []) if now - t < AUTH_FAIL_WINDOW]
        _auth_failures[ip] = recent
        return len(recent) >= AUTH_FAIL_LIMIT


def _record_auth_failure(ip: str) -> None:
    with _auth_lock:
        _auth_failures.setdefault(ip, []).append(time.monotonic())


def _admin_guard():
    """HTTP Basic auth guard. Returns a 401/429 response unless authorised, else None."""
    ip = _client_ip() or "unknown"
    if _auth_blocked(ip):
        return jsonify({"ok": False, "error": "Too many login attempts. Try again later."}), 429
    creds = _basic_credentials()
    if creds and hmac.compare_digest(creds[0], ADMIN_USER) and _verify_admin_password(creds[1]):
        with _auth_lock:
            _auth_failures.pop(ip, None)
        return None
    _record_auth_failure(ip)
    resp = jsonify({"ok": False, "error": "Unauthorized"})
    resp.status_code = 401
    resp.headers["WWW-Authenticate"] = 'Basic realm="stardust experiment console"'
    return resp


def _summary(events: list[dict], signups: list[dict]) -> dict:
    from collections import Counter

    def count(name: str) -> int:
        return sum(1 for e in events if e.get("event") == name)

    visits = count("visit")
    returns = count("return_visit")
    unique_visitors = len({e.get("visitor_id") for e in events if e.get("visitor_id")})
    leads = len(signups)
    email_submits = count("email_submit")

    sources = Counter(e.get("source") or "direct" for e in events)
    leads_by_source = Counter(s.get("source") or "direct" for s in signups)
    tickers = Counter(s.get("ticker") for s in signups if s.get("ticker"))

    def pct(num, den):
        return round(num / den * 100, 1) if den else 0.0

    return {
        "kpis": {
            "visits": visits + returns,
            "unique_visitors": unique_visitors,
            "return_visits": returns,
            "return_rate_pct": pct(returns, unique_visitors),
            "ticker_lookups": count("ticker_lookup"),
            "signal_views": count("signal_view"),
            "cta_clicks": count("cta_click"),
            "leads": leads,
            "email_submits": email_submits,
            "conversion_pct": pct(leads, unique_visitors),
            "wtp_yes": count("wtp_yes"),
            "wtp_maybe": count("wtp_maybe"),
            "wtp_no": count("wtp_no"),
        },
        "funnel": [
            {"step": "Visits", "value": visits + returns},
            {"step": "Ticker lookups", "value": count("ticker_lookup")},
            {"step": "Signal views", "value": count("signal_view")},
            {"step": "CTA clicks", "value": count("cta_click")},
            {"step": "Email submits", "value": email_submits},
        ],
        "sources": [{"source": s, "events": n, "leads": leads_by_source.get(s, 0)} for s, n in sources.most_common()],
        "top_tickers": [{"ticker": t, "leads": n} for t, n in tickers.most_common(10)],
    }


@app.get("/admin")
def admin_page():
    guard = _admin_guard()
    if guard:
        return guard
    return send_from_directory(BASE_DIR, "admin.html")


@app.get("/admin/portfolio")
def admin_portfolio_page():
    guard = _admin_guard()
    if guard:
        return guard
    return send_from_directory(BASE_DIR, "admin_portfolio.html")


@app.get("/api/admin/portfolio")
def admin_portfolio_data():
    """Serve the pre-generated portfolio-backtest payload (never runs the engine)."""
    guard = _admin_guard()
    if guard:
        return guard
    if not PORTFOLIO_JSON.exists():
        return jsonify({"ok": False, "available": False}), 404
    resp = send_file(PORTFOLIO_JSON, mimetype="application/json")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/admin/data")
def admin_data():
    guard = _admin_guard()
    if guard:
        return guard
    events = _read("events", EVENT_FIELDS)
    signups = _read("signups", SIGNUP_FIELDS)
    return jsonify(
        {
            "ok": True,
            "generated_at": _now(),
            "summary": _summary(events, signups),
            "signups": signups,
            "events": events,
        }
    )


_EXPORTS = {
    "signups.csv": (SIGNUP_FIELDS, "signups"),
    "events.csv": (EVENT_FIELDS, "events"),
}


@app.get("/api/admin/export/<name>")
def admin_export(name: str):
    guard = _admin_guard()
    if guard:
        return guard
    spec = _EXPORTS.get(name)
    if spec is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    fields, table = spec
    return Response(
        _to_csv(fields, _read(table, fields)),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={name}", "Cache-Control": "no-store"},
    )


# --------------------------------------------------------------------------- #
# Shadow-run journal — the operator's decisions and executed fills. Append-only;
# the offline snapshot generator reads it back through these guarded endpoints.
# --------------------------------------------------------------------------- #
@app.get("/api/admin/trade/fills")
def admin_journal_fills():
    guard = _admin_guard()
    if guard:
        return guard
    rows = _read("journal_fills", JOURNAL_FILL_FIELDS)
    return jsonify({"ok": True, "count": len(rows), "fills": rows})


@app.post("/api/admin/trade/fills")
def admin_journal_fill_create():
    guard = _admin_guard()
    if guard:
        return guard
    row, error = _fill_from_payload(_payload())
    if error:
        return jsonify({"ok": False, "error": error}), 400
    row_id = _insert_row("journal_fills", JOURNAL_FILL_FIELDS, row)
    return jsonify({"ok": True, "id": row_id, "fill": {"id": row_id, **row}}), 201


@app.get("/api/admin/trade/decisions")
def admin_journal_decisions():
    guard = _admin_guard()
    if guard:
        return guard
    rows = _read("journal_decisions", JOURNAL_DECISION_FIELDS)
    return jsonify({"ok": True, "count": len(rows), "decisions": rows})


@app.post("/api/admin/trade/decisions")
def admin_journal_decision_create():
    guard = _admin_guard()
    if guard:
        return guard
    row, error = _decision_from_payload(_payload())
    if error:
        return jsonify({"ok": False, "error": error}), 400
    row_id = _insert_row("journal_decisions", JOURNAL_DECISION_FIELDS, row)
    return jsonify({"ok": True, "id": row_id, "decision": {"id": row_id, **row}}), 201


_JOURNAL_EXPORTS = {
    "fills.csv": (JOURNAL_FILL_FIELDS, "journal_fills"),
    "decisions.csv": (JOURNAL_DECISION_FIELDS, "journal_decisions"),
}


@app.get("/api/admin/trade/export/<name>")
def admin_journal_export(name: str):
    guard = _admin_guard()
    if guard:
        return guard
    spec = _JOURNAL_EXPORTS.get(name)
    if spec is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    fields, table = spec
    return Response(
        _to_csv(fields, _read(table, fields)),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=journal_{name}",
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/admin/db")
def admin_db():
    """Download a consistent SQLite snapshot of the recorded leads/events."""
    guard = _admin_guard()
    if guard:
        return guard
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DATA_DIR / f".snapshot-{os.getpid()}-{secrets.token_hex(4)}.db"
    conn = _open()
    try:
        if tmp.exists():
            tmp.unlink()
        conn.execute("VACUUM INTO ?", (str(tmp),))
    finally:
        conn.close()
    data = tmp.read_bytes()
    tmp.unlink()
    name = f"stardust-landing-{datetime.now(timezone.utc).date().isoformat()}.db"
    return Response(
        data,
        mimetype="application/vnd.sqlite3",
        headers={"Content-Disposition": f"attachment; filename={name}", "Cache-Control": "no-store"},
    )


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/<path:filename>")
def static_files(filename: str):
    if (
        filename.startswith("api/")
        or filename.startswith("data/")
        or filename.startswith("private/")
        or filename == "admin"
    ):
        return jsonify({"ok": False, "error": "not found"}), 404
    return send_from_directory(BASE_DIR, filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
