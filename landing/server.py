"""Minimal backend for the stardust signals landing-page experiment.

Serves the static landing page and records the two things the experiment
actually measures:

* ``POST /api/signup`` — a visitor traded their email for a ticker's signal
  (the primary conversion event).
* ``POST /api/track``  — a funnel/behaviour event (visit, ticker_lookup,
  signal_view, cta_click, return_visit, willingness-to-pay clicks...).

Everything is written to plain CSV files under ``landing/data/`` so a
lightweight spreadsheet/DB is the only "backend" needed. No third-party
analytics required; drop in PostHog/Plausible in ``app.js`` if you want more.

Run locally:
    python landing/server.py            # http://127.0.0.1:8000

Run in production (AWS):
    gunicorn --bind 0.0.0.0:8000 --chdir landing server:app
"""

from __future__ import annotations

import csv
import hmac
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory, session

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("LANDING_DATA_DIR", BASE_DIR / "data"))
SIGNUPS_CSV = DATA_DIR / "signups.csv"
EVENTS_CSV = DATA_DIR / "events.csv"
DEMO_JSON = BASE_DIR / "demo_signals.json"

# Simple PIN gate for the /admin console. Change it before deploying:
#     LANDING_ADMIN_PIN=2468 LANDING_SECRET_KEY=$(openssl rand -hex 32) ...
ADMIN_PIN = os.environ.get("LANDING_ADMIN_PIN", "5073")
# Signs the admin session cookie. Generated per-process if not supplied, which
# only means sessions don't survive a restart (fine for a short experiment).
SECRET_KEY = os.environ.get("LANDING_SECRET_KEY") or secrets.token_hex(32)

DATA_DIR.mkdir(parents=True, exist_ok=True)

SIGNUP_FIELDS = [
    "timestamp", "email", "ticker", "source", "utm_medium", "utm_campaign",
    "referrer", "visitor_id", "is_return", "user_agent", "ip",
]
EVENT_FIELDS = [
    "timestamp", "event", "ticker", "source", "visitor_id", "is_return",
    "path", "referrer", "meta",
]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TICKER_RE = re.compile(r"^[A-Z.\-]{1,6}$")
_write_lock = threading.Lock()

app = Flask(__name__, static_folder=None)
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("LANDING_COOKIE_SECURE") == "1",
)
try:  # honour X-Forwarded-* when running behind nginx / a load balancer
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
except Exception:  # pragma: no cover - werkzeug always ships ProxyFix
    pass


def _append(path: Path, fields: list[str], row: dict) -> None:
    """Append one row to a CSV, writing the header on first write."""
    with _write_lock:
        is_new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            if is_new:
                writer.writeheader()
            writer.writerow(row)


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

    _append(
        SIGNUPS_CSV,
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
    _append(
        EVENTS_CSV,
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


# --------------------------------------------------------------------------- #
# Admin view — read-only dashboard over the recorded CSVs.
# --------------------------------------------------------------------------- #
def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _admin_ok() -> bool:
    return session.get("admin") is True


def _admin_guard():
    """Return a 401 response unless the session has unlocked the console."""
    if not _admin_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return None


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
    return send_from_directory(BASE_DIR, "admin.html")


@app.post("/api/admin/login")
def admin_login():
    pin = str(_payload().get("pin", ""))
    if hmac.compare_digest(pin, ADMIN_PIN):
        session["admin"] = True
        session.permanent = False
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Incorrect PIN."}), 401


@app.post("/api/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return jsonify({"ok": True})


@app.get("/api/admin/session")
def admin_session():
    return jsonify({"ok": True, "unlocked": _admin_ok()})


@app.get("/api/admin/data")
def admin_data():
    guard = _admin_guard()
    if guard:
        return guard
    events = _read_csv(EVENTS_CSV)
    signups = _read_csv(SIGNUPS_CSV)
    return jsonify(
        {
            "ok": True,
            "generated_at": _now(),
            "summary": _summary(events, signups),
            "signups": list(reversed(signups)),
            "events": list(reversed(events)),
        }
    )


@app.get("/api/admin/export/<name>")
def admin_export(name: str):
    guard = _admin_guard()
    if guard:
        return guard
    files = {"signups.csv": SIGNUPS_CSV, "events.csv": EVENTS_CSV}
    path = files.get(name)
    if path is None or not path.exists():
        return jsonify({"ok": False, "error": "not found"}), 404
    return Response(
        path.read_text(encoding="utf-8"),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={name}"},
    )


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/<path:filename>")
def static_files(filename: str):
    if filename.startswith("api/") or filename == "admin":
        return jsonify({"ok": False, "error": "not found"}), 404
    return send_from_directory(BASE_DIR, filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
