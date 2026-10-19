"""Tests for landing/server.py — the experiment backend.

Unit tests cover the pure helpers (summary computation, bool parsing). The
integration tests drive the real Flask app with its test client and assert on
the SQLite rows it writes, so the full signup / tracking / admin funnel is
exercised end to end with no network and no real lead data.
"""

from __future__ import annotations

import base64
import csv
import json
import os
import re
import sqlite3

import pytest

from landing import gen_admin_password, server


# --------------------------------------------------------------------------- #
# Unit helpers
# --------------------------------------------------------------------------- #
class TestBool:
    @pytest.mark.parametrize("value", [True, "true", "True", "1", "yes"])
    def test_truthy(self, value):
        assert server._bool(value) == "true"

    @pytest.mark.parametrize("value", [False, "false", "0", "no", "", None, 2])
    def test_falsy(self, value):
        assert server._bool(value) == "false"


class TestSummary:
    def _events(self):
        return [
            {"event": "visit", "visitor_id": "a", "source": "reddit"},
            {"event": "visit", "visitor_id": "b", "source": "reddit"},
            {"event": "return_visit", "visitor_id": "a", "source": "direct"},
            {"event": "ticker_lookup", "visitor_id": "a", "source": "reddit"},
            {"event": "signal_view", "visitor_id": "a", "source": "reddit"},
            {"event": "cta_click", "visitor_id": "a", "source": "reddit"},
            {"event": "email_submit", "visitor_id": "a", "source": "reddit"},
            {"event": "wtp_yes", "visitor_id": "a", "source": "reddit"},
            {"event": "wtp_maybe", "visitor_id": "b", "source": "reddit"},
        ]

    def test_kpis(self):
        summary = server._summary(self._events(), [{"source": "reddit", "ticker": "AAPL"}])
        k = summary["kpis"]
        assert k["visits"] == 3  # visit x2 + return_visit
        assert k["unique_visitors"] == 2
        assert k["return_visits"] == 1
        assert k["return_rate_pct"] == 50.0
        assert k["ticker_lookups"] == 1
        assert k["signal_views"] == 1
        assert k["cta_clicks"] == 1
        assert k["leads"] == 1
        assert k["email_submits"] == 1
        assert k["conversion_pct"] == 50.0
        assert k["wtp_yes"] == 1 and k["wtp_maybe"] == 1 and k["wtp_no"] == 0

    def test_funnel_order(self):
        funnel = server._summary(self._events(), [{"source": "reddit", "ticker": "AAPL"}])["funnel"]
        assert [f["step"] for f in funnel] == [
            "Visits", "Ticker lookups", "Signal views", "CTA clicks", "Email submits",
        ]

    def test_sources_and_tickers(self):
        events = self._events() + [{"event": "visit", "visitor_id": "c", "source": "hn"}]
        summary = server._summary(
            events,
            [{"source": "reddit", "ticker": "AAPL"}, {"source": "hn", "ticker": "AAPL"}],
        )
        src = {s["source"]: s for s in summary["sources"]}
        assert src["reddit"]["leads"] == 1
        assert src["hn"]["leads"] == 1
        assert summary["top_tickers"][0] == {"ticker": "AAPL", "leads": 2}

    def test_empty_inputs(self):
        summary = server._summary([], [])
        assert summary["kpis"]["leads"] == 0
        assert summary["kpis"]["conversion_pct"] == 0.0
        assert summary["sources"] == []


class TestToCsv:
    def test_roundtrip_with_bom(self):
        text = server._to_csv(server.SIGNUP_FIELDS, [{"email": "a@b.com", "ticker": "AAPL"}])
        assert text.startswith("\ufeff")
        rows = list(csv.DictReader(text.lstrip("\ufeff").splitlines()))
        assert rows == [{"timestamp": "", "email": "a@b.com", "ticker": "AAPL", "source": "",
                         "utm_medium": "", "utm_campaign": "", "referrer": "", "visitor_id": "",
                         "is_return": "", "user_agent": "", "ip": ""}]


# --------------------------------------------------------------------------- #
# Integration — Flask test client
# --------------------------------------------------------------------------- #
USER = "admin"
PASSWORD = "s3cret-pass"


def _auth(user=USER, password=PASSWORD):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "landing.db")
    monkeypatch.setattr(server, "SIGNUPS_CSV", tmp_path / "signups.csv")
    monkeypatch.setattr(server, "EVENTS_CSV", tmp_path / "events.csv")
    monkeypatch.setattr(server, "ADMIN_USER", USER)
    monkeypatch.setattr(server, "ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setattr(server, "ADMIN_PASS_HASH", "")
    monkeypatch.setattr(server, "_initialized", set())
    monkeypatch.setattr(server, "_auth_failures", {})
    server.app.config.update(TESTING=True)
    with server.app.test_client() as c:
        yield c


def _db_rows(table, fields):
    return server._read(table, fields)


class TestStaticRoutes:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_index_served(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Stop guessing" in resp.data

    def test_index_copy_leads_with_out_of_sample(self, client):
        html = client.get("/").data.decode("utf-8")
        assert "whether it still works" in html
        # Prose must not leak the em-dash machine-writing tell (comments are not
        # visitor-facing, so strip them before checking).
        prose = re.sub(r"<!--.*?-->", "", html, flags=re.S)
        assert "\u2014" not in prose

    def test_admin_page_requires_auth(self, client):
        resp = client.get("/admin")
        assert resp.status_code == 401
        assert "Basic" in resp.headers["WWW-Authenticate"]

    def test_admin_page_served_with_auth(self, client):
        resp = client.get("/admin", headers=_auth())
        assert resp.status_code == 200
        assert b"experiment console" in resp.data

    def test_static_asset_served(self, client):
        assert client.get("/styles.css").status_code == 200

    def test_unknown_file_404(self, client):
        assert client.get("/does-not-exist.css").status_code == 404

    def test_unknown_api_path_404(self, client):
        assert client.get("/api/nope").status_code == 404

    def test_ticker_index_served(self, client):
        resp = client.get("/tickers.json")
        assert resp.status_code == 200
        tickers = {e["ticker"] for e in resp.get_json()["tickers"]}
        assert "AAPL" in tickers


class TestDemoEndpoint:
    @pytest.fixture
    def demo_file(self, tmp_path, monkeypatch):
        path = tmp_path / "demo_signals.json"
        path.write_text(json.dumps({
            "generated_at": "2026-01-01T00:00:00+00:00",
            "oos_definition": "holdout",
            "tickers": {"AAPL": {"name": "Apple Inc.", "signal": {"direction": "BUY"}, "windows": {}}},
        }))
        monkeypatch.setattr(server, "DEMO_JSON", path)
        return path

    def test_known_ticker(self, client, demo_file):
        body = client.get("/api/demo?ticker=aapl").get_json()
        assert body["available"] is True
        assert body["data"]["name"] == "Apple Inc."
        assert "AAPL" in body["known"]

    def test_exposes_analysis_date_and_oos_definition(self, client, demo_file):
        body = client.get("/api/demo?ticker=AAPL").get_json()
        assert body["generated_at"] == "2026-01-01T00:00:00+00:00"
        assert body["oos_definition"] == "holdout"

    def test_unknown_ticker(self, client, demo_file):
        body = client.get("/api/demo?ticker=ZZZZ").get_json()
        assert body["available"] is False
        assert body["data"] is None

    def test_missing_demo_file(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "DEMO_JSON", tmp_path / "missing.json")
        body = client.get("/api/demo?ticker=AAPL").get_json()
        assert body["available"] is False

    def test_metadata_without_ticker(self, client, demo_file):
        # The page pre-loads /api/demo?ticker= to get the known list + analysis date.
        body = client.get("/api/demo").get_json()
        assert body["available"] is False
        assert body["data"] is None
        assert body["generated_at"] == "2026-01-01T00:00:00+00:00"
        assert "AAPL" in body["known"]


class TestTradesEndpoint:
    COLUMNS = ["Entry Date", "Entry Price", "Exit Date", "Exit Price",
               "P&L ($)", "P&L (%)", "Holding (days)", "Exit Reason"]

    @pytest.fixture
    def trades_file(self, tmp_path, monkeypatch):
        path = tmp_path / "demo_trades.json"
        path.write_text(json.dumps({
            "generated_at": "2026-01-01T00:00:00+00:00",
            "columns": self.COLUMNS,
            "trades": {"AAPL": {"1y": [["2024-01-02", 100.0, "2024-01-20", 110.0,
                                       1000.0, 10.0, 18, "signal"]]}},
        }))
        monkeypatch.setattr(server, "TRADES_JSON", path)
        monkeypatch.setattr(server, "_trades_cache", {"mtime": None, "payload": None})
        return path

    def test_known_ticker(self, client, trades_file):
        body = client.get("/api/trades?ticker=aapl").get_json()
        assert body["available"] is True
        assert body["ticker"] == "AAPL"
        assert body["columns"] == self.COLUMNS
        assert body["windows"]["1y"][0][0] == "2024-01-02"

    def test_unknown_ticker(self, client, trades_file):
        body = client.get("/api/trades?ticker=ZZZZ").get_json()
        assert body["available"] is False
        assert body["windows"] == {}

    def test_missing_file(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "TRADES_JSON", tmp_path / "missing.json")
        monkeypatch.setattr(server, "_trades_cache", {"mtime": None, "payload": None})
        body = client.get("/api/trades?ticker=AAPL").get_json()
        assert body["available"] is False

    def test_includes_generated_at(self, client, trades_file):
        body = client.get("/api/trades?ticker=AAPL").get_json()
        assert body["generated_at"] == "2026-01-01T00:00:00+00:00"

    def test_result_cached_between_requests(self, client, trades_file):
        client.get("/api/trades?ticker=AAPL")
        cached = server._trades_cache["payload"]
        client.get("/api/trades?ticker=AAPL")
        assert server._trades_cache["payload"] is cached  # parsed once, not re-read

    def test_result_reloads_when_file_changes(self, client, trades_file):
        assert client.get("/api/trades?ticker=AAPL").get_json()["windows"]["1y"][0][0] == "2024-01-02"
        trades_file.write_text(json.dumps({
            "generated_at": "2026-02-02T00:00:00+00:00",
            "columns": self.COLUMNS,
            "trades": {"AAPL": {"1y": [["2025-06-01", 1.0, "2025-06-05", 2.0, 9.0, 9.0, 4, "stop"]]}},
        }))
        newer = trades_file.stat().st_mtime + 10
        os.utime(trades_file, (newer, newer))
        body = client.get("/api/trades?ticker=AAPL").get_json()
        assert body["windows"]["1y"][0][0] == "2025-06-01"
        assert body["generated_at"] == "2026-02-02T00:00:00+00:00"

    def test_malformed_file_is_unavailable(self, client, tmp_path, monkeypatch):
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json")
        monkeypatch.setattr(server, "TRADES_JSON", bad)
        monkeypatch.setattr(server, "_trades_cache", {"mtime": None, "payload": None})
        body = client.get("/api/trades?ticker=AAPL").get_json()
        assert body["available"] is False
        assert body["windows"] == {}


class TestLoadTrades:
    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "TRADES_JSON", tmp_path / "nope.json")
        monkeypatch.setattr(server, "_trades_cache", {"mtime": None, "payload": None})
        assert server._load_trades() is None

    def test_parses_and_caches(self, tmp_path, monkeypatch):
        path = tmp_path / "t.json"
        path.write_text(json.dumps({"columns": [], "trades": {}}))
        monkeypatch.setattr(server, "TRADES_JSON", path)
        monkeypatch.setattr(server, "_trades_cache", {"mtime": None, "payload": None})
        first = server._load_trades()
        assert first == {"columns": [], "trades": {}}
        assert server._load_trades() is first


class TestQuoteEndpoint:
    GOOD = {
        "ticker": "AAPL", "price": 175.5, "previous_close": 174.0,
        "change": 1.5, "change_pct": 0.008621, "currency": "USD",
        "exchange": "NasdaqGS", "as_of": "2026-10-15T20:00:00+00:00",
    }

    @pytest.fixture(autouse=True)
    def clear_quote_cache(self, monkeypatch):
        monkeypatch.setattr(server, "_quote_cache", {})
        monkeypatch.setattr(server, "_quote_fail_cache", {})

    def test_invalid_ticker_is_rejected_without_calling_yahoo(self, client, monkeypatch):
        called = []
        monkeypatch.setattr(server, "_fetch_quote", lambda t: called.append(t) or None)
        resp = client.get("/api/quote?ticker=%3Cscript%3E")
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False
        assert called == []

    def test_valid_ticker_returns_a_quote(self, client, monkeypatch):
        monkeypatch.setattr(server, "_fetch_quote", lambda t: dict(self.GOOD))
        resp = client.get("/api/quote?ticker=aapl")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["ticker"] == "AAPL"
        assert body["quote"]["price"] == 175.5

    def test_upstream_failure_is_a_502(self, client, monkeypatch):
        monkeypatch.setattr(server, "_fetch_quote", lambda t: None)
        resp = client.get("/api/quote?ticker=AAPL")
        assert resp.status_code == 502
        assert resp.get_json()["ok"] is False

    def test_result_is_cached_between_requests(self, client, monkeypatch):
        calls = []
        monkeypatch.setattr(server, "_fetch_quote", lambda t: calls.append(t) or dict(self.GOOD))
        client.get("/api/quote?ticker=AAPL")
        client.get("/api/quote?ticker=AAPL")
        assert calls == ["AAPL"]

    def test_failures_are_cached_briefly(self, client, monkeypatch):
        calls = []
        monkeypatch.setattr(server, "_fetch_quote", lambda t: calls.append(t) or None)
        client.get("/api/quote?ticker=AAPL")
        client.get("/api/quote?ticker=AAPL")
        assert calls == ["AAPL"]

    def test_a_later_success_clears_the_failure_cache(self, client, monkeypatch):
        healthy = {"state": False}

        def fetch(t):
            return dict(self.GOOD) if healthy["state"] else None

        monkeypatch.setattr(server, "_fetch_quote", fetch)
        assert client.get("/api/quote?ticker=AAPL").status_code == 502
        healthy["state"] = True
        server._quote_fail_cache.clear()  # simulate the 30s failure window elapsing
        body = client.get("/api/quote?ticker=AAPL").get_json()
        assert body["ok"] is True
        assert "AAPL" not in server._quote_fail_cache


class TestFetchQuote:
    def _response(self, monkeypatch, payload):
        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        monkeypatch.setattr(server.urllib.request, "urlopen", lambda *a, **k: FakeResp())

    def test_parses_price_and_change(self, monkeypatch):
        self._response(monkeypatch, {"chart": {"result": [{"meta": {
            "symbol": "AAPL", "regularMarketPrice": 180.0, "chartPreviousClose": 175.0,
            "currency": "USD", "exchangeName": "NasdaqGS", "regularMarketTime": 1_700_000_000,
        }}]}})
        quote = server._fetch_quote("AAPL")
        assert quote["price"] == 180.0
        assert quote["previous_close"] == 175.0
        assert quote["change"] == 5.0
        assert quote["change_pct"] == pytest.approx(5.0 / 175.0, abs=1e-6)
        assert quote["as_of"].startswith("2023-11-14T")

    def test_empty_result_is_none(self, monkeypatch):
        self._response(monkeypatch, {"chart": {"result": None}})
        assert server._fetch_quote("AAPL") is None

    def test_missing_price_is_none(self, monkeypatch):
        self._response(monkeypatch, {"chart": {"result": [{"meta": {"symbol": "AAPL"}}]}})
        assert server._fetch_quote("AAPL") is None

    def test_network_error_is_none(self, monkeypatch):
        def boom(*a, **k):
            raise server.urllib.error.URLError("no network")

        monkeypatch.setattr(server.urllib.request, "urlopen", boom)
        assert server._fetch_quote("AAPL") is None

    def test_malformed_json_is_none(self, monkeypatch):
        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"{not valid json"

        monkeypatch.setattr(server.urllib.request, "urlopen", lambda *a, **k: FakeResp())
        assert server._fetch_quote("AAPL") is None

    def test_falls_back_to_previous_close(self, monkeypatch):
        self._response(monkeypatch, {"chart": {"result": [{"meta": {
            "symbol": "AAPL", "regularMarketPrice": 180.0, "previousClose": 175.0,
        }}]}})
        quote = server._fetch_quote("AAPL")
        assert quote["previous_close"] == 175.0
        assert quote["change"] == 5.0
        assert quote["change_pct"] == pytest.approx(5.0 / 175.0, abs=1e-6)


class TestSignup:
    def test_valid_signup_writes_row(self, client):
        resp = client.post("/api/signup", json={
            "email": "Person@Example.com", "ticker": "aapl",
            "source": "reddit_algotrading", "visitor_id": "v1", "is_return": False,
        })
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        rows = _db_rows("signups", server.SIGNUP_FIELDS)
        assert len(rows) == 1
        assert rows[0]["email"] == "person@example.com"  # normalised
        assert rows[0]["ticker"] == "AAPL"               # upper-cased
        assert rows[0]["source"] == "reddit_algotrading"
        assert rows[0]["is_return"] == "false"

    @pytest.mark.parametrize("email", ["", "not-an-email", "a@b", "no@dot@x.com"])
    def test_invalid_email_rejected(self, client, email):
        resp = client.post("/api/signup", json={"email": email, "ticker": "AAPL"})
        assert resp.status_code == 400
        assert _db_rows("signups", server.SIGNUP_FIELDS) == []

    def test_invalid_ticker_rejected(self, client):
        resp = client.post("/api/signup", json={"email": "a@b.com", "ticker": "TOOLONGTICKER"})
        assert resp.status_code == 400

    def test_ticker_optional(self, client):
        resp = client.post("/api/signup", json={"email": "a@b.com"})
        assert resp.status_code == 200
        assert _db_rows("signups", server.SIGNUP_FIELDS)[0]["ticker"] == ""

    def test_records_user_agent_and_ip(self, client):
        client.post(
            "/api/signup",
            json={"email": "a@b.com"},
            headers={"User-Agent": "pytest-agent/1.0", "X-Forwarded-For": "203.0.113.7, 10.0.0.1"},
        )
        row = _db_rows("signups", server.SIGNUP_FIELDS)[0]
        assert row["user_agent"] == "pytest-agent/1.0"
        assert row["ip"] == "203.0.113.7"  # first hop of X-Forwarded-For


class TestTrack:
    def test_valid_event_written(self, client):
        resp = client.post("/api/track", json={
            "event": "ticker_lookup", "ticker": "aapl", "source": "hn",
            "visitor_id": "v1", "meta": {"known": True},
        })
        assert resp.status_code == 200
        rows = _db_rows("events", server.EVENT_FIELDS)
        assert rows[0]["event"] == "ticker_lookup"
        assert rows[0]["ticker"] == "AAPL"
        assert json.loads(rows[0]["meta"]) == {"known": True}

    def test_event_required(self, client):
        assert client.post("/api/track", json={"ticker": "AAPL"}).status_code == 400

    def test_records_path_and_returning(self, client):
        client.post("/api/track", json={
            "event": "return_visit", "visitor_id": "v1", "path": "/offer", "is_return": True,
        })
        row = _db_rows("events", server.EVENT_FIELDS)[0]
        assert row["path"] == "/offer"
        assert row["is_return"] == "true"

    def test_newest_first(self, client):
        client.post("/api/track", json={"event": "visit"})
        client.post("/api/track", json={"event": "cta_click"})
        assert [r["event"] for r in _db_rows("events", server.EVENT_FIELDS)] == ["cta_click", "visit"]


class TestDatabase:
    def test_schema_tables(self, client):
        conn = server._open()
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        assert {"signups", "events"} <= names

    def test_wal_journal_mode(self, client):
        conn = server._open()
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        finally:
            conn.close()

    def test_read_empty_table(self, client):
        assert _db_rows("signups", server.SIGNUP_FIELDS) == []
        assert _db_rows("events", server.EVENT_FIELDS) == []

    def test_insert_read_roundtrip(self, client):
        server._insert("signups", server.SIGNUP_FIELDS, {
            "timestamp": "2026-01-01T00:00:00+00:00", "email": "x@y.com", "ticker": "MSFT",
            "source": "direct", "utm_medium": "", "utm_campaign": "", "referrer": "",
            "visitor_id": "v1", "is_return": "false", "user_agent": "", "ip": "1.2.3.4",
        })
        row = _db_rows("signups", server.SIGNUP_FIELDS)[0]
        assert row["email"] == "x@y.com" and row["ticker"] == "MSFT"
        assert "id" in row  # ids are exposed to the admin UI / exports


class TestVerifyAdminPassword:
    def test_plain(self, monkeypatch):
        monkeypatch.setattr(server, "ADMIN_PASSWORD", "pw")
        monkeypatch.setattr(server, "ADMIN_PASS_HASH", "")
        assert server._verify_admin_password("pw") is True
        assert server._verify_admin_password("nope") is False

    def test_hash(self, monkeypatch):
        monkeypatch.setattr(server, "ADMIN_PASSWORD", "")
        monkeypatch.setattr(server, "ADMIN_PASS_HASH", gen_admin_password.make_hash("pw"))
        assert server._verify_admin_password("pw") is True
        assert server._verify_admin_password("nope") is False

    def test_none_configured(self, monkeypatch):
        monkeypatch.setattr(server, "ADMIN_PASSWORD", "")
        monkeypatch.setattr(server, "ADMIN_PASS_HASH", "")
        assert server._verify_admin_password("") is False

    def test_malformed_hash(self, monkeypatch):
        monkeypatch.setattr(server, "ADMIN_PASSWORD", "")
        monkeypatch.setattr(server, "ADMIN_PASS_HASH", "not-a-valid-hash")
        assert server._verify_admin_password("x") is False


class TestAdminAuth:
    def test_data_requires_auth(self, client):
        resp = client.get("/api/admin/data")
        assert resp.status_code == 401
        assert "Basic" in resp.headers["WWW-Authenticate"]

    def test_wrong_password_rejected(self, client):
        assert client.get("/api/admin/data", headers=_auth(password="nope")).status_code == 401

    def test_wrong_user_rejected(self, client):
        assert client.get("/api/admin/data", headers=_auth(user="root")).status_code == 401

    def test_valid_credentials_read(self, client):
        body = client.get("/api/admin/data", headers=_auth()).get_json()
        assert body["ok"] is True
        assert "summary" in body and "signups" in body and "events" in body

    def test_malformed_authorization_rejected(self, client):
        assert client.get("/api/admin/data", headers={"Authorization": "Basic !!!"}).status_code == 401

    def test_lockout_after_repeated_failures(self, client):
        for _ in range(server.AUTH_FAIL_LIMIT):
            assert client.get("/api/admin/data", headers=_auth(password="bad")).status_code == 401
        # Even correct credentials are refused once the IP is locked out.
        locked = client.get("/api/admin/data", headers=_auth())
        assert locked.status_code == 429

    def test_lockout_is_per_ip(self, client):
        bad_ip = {"X-Forwarded-For": "198.51.100.9"}
        for _ in range(server.AUTH_FAIL_LIMIT):
            client.get("/api/admin/data", headers={**_auth(password="bad"), **bad_ip})
        assert client.get("/api/admin/data", headers={**_auth(), **bad_ip}).status_code == 429
        # A different client IP is unaffected.
        other = client.get("/api/admin/data", headers={**_auth(), "X-Forwarded-For": "203.0.113.1"})
        assert other.status_code == 200

    def test_success_resets_failure_counter(self, client):
        for _ in range(server.AUTH_FAIL_LIMIT - 1):
            assert client.get("/api/admin/data", headers=_auth(password="bad")).status_code == 401
        assert client.get("/api/admin/data", headers=_auth()).status_code == 200
        # The counter was cleared, so we get a fresh budget of failures.
        for _ in range(server.AUTH_FAIL_LIMIT - 1):
            assert client.get("/api/admin/data", headers=_auth(password="bad")).status_code == 401

    def test_hash_auth(self, client, monkeypatch):
        monkeypatch.setattr(server, "ADMIN_PASSWORD", "")
        monkeypatch.setattr(server, "ADMIN_PASS_HASH", gen_admin_password.make_hash(PASSWORD))
        assert client.get("/api/admin/data", headers=_auth()).status_code == 200
        assert client.get("/api/admin/data", headers=_auth(password="wrong")).status_code == 401

    def test_no_password_configured_denies(self, client, monkeypatch):
        monkeypatch.setattr(server, "ADMIN_PASSWORD", "")
        monkeypatch.setattr(server, "ADMIN_PASS_HASH", "")
        assert client.get("/api/admin/data", headers=_auth()).status_code == 401

    def test_export_guarded(self, client):
        assert client.get("/api/admin/export/signups.csv").status_code == 401

    def test_export_with_auth(self, client):
        client.post("/api/signup", json={"email": "a@b.com", "ticker": "AAPL"})
        resp = client.get("/api/admin/export/signups.csv", headers=_auth())
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["Content-Type"]
        assert b"a@b.com" in resp.data

    def test_export_events_with_auth(self, client):
        client.post("/api/track", json={"event": "visit", "source": "reddit"})
        resp = client.get("/api/admin/export/events.csv", headers=_auth())
        assert resp.status_code == 200
        rows = list(csv.DictReader(resp.data.decode("utf-8-sig").splitlines()))
        assert rows and rows[0]["event"] == "visit" and rows[0]["source"] == "reddit"

    def test_export_unknown_name_404(self, client):
        assert client.get("/api/admin/export/whatever.csv", headers=_auth()).status_code == 404

    def test_db_snapshot_guarded(self, client):
        assert client.get("/api/admin/db").status_code == 401

    def test_db_snapshot_with_auth(self, client, tmp_path):
        client.post("/api/signup", json={"email": "a@b.com", "ticker": "AAPL"})
        resp = client.get("/api/admin/db", headers=_auth())
        assert resp.status_code == 200
        assert resp.data[:16] == b"SQLite format 3\x00"  # a real, valid SQLite file
        assert "attachment" in resp.headers["Content-Disposition"]
        # The snapshot is a normal SQLite DB we can open and query independently.
        snap = tmp_path / "snapshot.db"
        snap.write_bytes(resp.data)
        conn = sqlite3.connect(snap)
        try:
            assert conn.execute("SELECT COUNT(*) FROM signups").fetchone()[0] == 1
        finally:
            conn.close()


class TestPasswordHash:
    def test_make_and_verify(self):
        value = gen_admin_password.make_hash("hunter2")
        assert value.count(":") == 1
        assert gen_admin_password.verify_hash("hunter2", value) is True
        assert gen_admin_password.verify_hash("Hunter2", value) is False

    def test_verify_rejects_malformed(self):
        assert gen_admin_password.verify_hash("x", "no-colon") is False
        assert gen_admin_password.verify_hash("x", "salt:zzzz") is False

    def test_salt_is_random(self):
        assert gen_admin_password.make_hash("pw") != gen_admin_password.make_hash("pw")

    def test_cli_prints_hash(self, capsys):
        assert gen_admin_password.main(["gen", "hunter2"]) == 0
        out = capsys.readouterr().out.strip()
        assert out.startswith("ADMIN_PASS_HASH=")
        assert gen_admin_password.verify_hash("hunter2", out.split("=", 1)[1]) is True

    def test_cli_env_fallback(self, capsys, monkeypatch):
        monkeypatch.setenv("PASSWORD", "envpass")
        assert gen_admin_password.main(["gen"]) == 0
        out = capsys.readouterr().out.strip()
        assert gen_admin_password.verify_hash("envpass", out.split("=", 1)[1]) is True

    def test_cli_usage_error(self, capsys, monkeypatch):
        monkeypatch.delenv("PASSWORD", raising=False)
        assert gen_admin_password.main(["gen"]) == 1
        assert "Usage" in capsys.readouterr().err


class TestEndToEndFunnel:
    def test_full_funnel_reflected_in_admin(self, client):
        # A visitor arrives from Reddit and moves through the funnel.
        client.post("/api/track", json={"event": "visit", "visitor_id": "v1", "source": "reddit"})
        client.post("/api/track", json={"event": "ticker_lookup", "visitor_id": "v1", "ticker": "AAPL", "source": "reddit"})
        client.post("/api/track", json={"event": "signal_view", "visitor_id": "v1", "ticker": "AAPL", "source": "reddit"})
        client.post("/api/signup", json={"email": "lead@example.com", "ticker": "AAPL", "visitor_id": "v1", "source": "reddit"})
        client.post("/api/track", json={"event": "email_submit", "visitor_id": "v1", "ticker": "AAPL", "source": "reddit"})
        client.post("/api/track", json={"event": "wtp_yes", "visitor_id": "v1", "source": "reddit"})

        data = client.get("/api/admin/data", headers=_auth()).get_json()

        assert data["summary"]["kpis"]["leads"] == 1
        assert data["summary"]["kpis"]["conversion_pct"] == 100.0
        assert data["summary"]["kpis"]["wtp_yes"] == 1
        assert data["summary"]["top_tickers"][0]["ticker"] == "AAPL"
        assert data["signups"][0]["email"] == "lead@example.com"
        # newest-first ordering
        assert data["events"][0]["event"] == "wtp_yes"


class TestLegacyMigration:
    def test_imports_existing_csv_once(self, client, tmp_path):
        (tmp_path / "signups.csv").write_text(
            "timestamp,email,ticker,source,utm_medium,utm_campaign,referrer,visitor_id,is_return,user_agent,ip\n"
            "2026-01-01T00:00:00+00:00,old@example.com,MSFT,hn,,,,v9,false,,1.2.3.4\n"
        )
        (tmp_path / "events.csv").write_text(
            "timestamp,event,ticker,source,visitor_id,is_return,path,referrer,meta\n"
            "2026-01-01T00:00:00+00:00,visit,,hn,v9,false,/,,{}\n"
        )
        server._open().close()  # trigger one-time migration

        assert _db_rows("signups", server.SIGNUP_FIELDS)[0]["email"] == "old@example.com"
        assert _db_rows("events", server.EVENT_FIELDS)[0]["source"] == "hn"

    def test_migration_is_idempotent(self, client, tmp_path):
        (tmp_path / "signups.csv").write_text(
            "timestamp,email,ticker,source,utm_medium,utm_campaign,referrer,visitor_id,is_return,user_agent,ip\n"
            "2026-01-01T00:00:00+00:00,old@example.com,MSFT,hn,,,,v9,false,,1.2.3.4\n"
        )
        server._open().close()          # imports the one row
        server._initialized.clear()     # force a second migration pass
        server._open().close()          # must skip: the table already has rows
        assert len(_db_rows("signups", server.SIGNUP_FIELDS)) == 1


class TestPortfolioRoutes:
    def test_portfolio_page_requires_auth(self, client):
        resp = client.get("/admin/portfolio")
        assert resp.status_code == 401
        assert "Basic" in resp.headers["WWW-Authenticate"]

    def test_portfolio_page_served_with_auth(self, client):
        resp = client.get("/admin/portfolio", headers=_auth())
        assert resp.status_code == 200
        assert b"portfolio console" in resp.data

    def test_portfolio_data_requires_auth(self, client):
        resp = client.get("/api/admin/portfolio")
        assert resp.status_code == 401

    def test_portfolio_data_missing_file(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "PORTFOLIO_JSON", tmp_path / "absent.json")
        resp = client.get("/api/admin/portfolio", headers=_auth())
        assert resp.status_code == 404
        assert resp.get_json()["available"] is False

    def test_portfolio_data_served_with_auth(self, client, tmp_path, monkeypatch):
        payload = {
            "generated_at": "2026-10-18T00:00:00+00:00",
            "runs": [{"id": "r1", "label": "Run 1", "trades": []}],
        }
        path = tmp_path / "portfolio_data.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(server, "PORTFOLIO_JSON", path)
        resp = client.get("/api/admin/portfolio", headers=_auth())
        assert resp.status_code == 200
        assert resp.headers["Cache-Control"] == "no-store"
        assert resp.get_json()["runs"][0]["id"] == "r1"

    def test_portfolio_data_not_in_public_static(self, client):
        assert client.get("/private/portfolio_data.json").status_code == 404

    def test_runtime_data_dir_not_served(self, client):
        assert client.get("/data/landing.db").status_code == 404

    def test_committed_payload_is_valid_if_present(self, client):
        if not server.PORTFOLIO_JSON.exists():
            pytest.skip("portfolio_data.json not generated")
        resp = client.get("/api/admin/portfolio", headers=_auth())
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["runs"]
        run = data["runs"][0]
        for key in ("id", "label", "config", "metrics", "equity", "daily", "trades"):
            assert key in run


class TestPortfolioSnapshotRoutes:
    def test_snapshot_requires_auth(self, client):
        assert client.get("/api/admin/portfolio/snapshot").status_code == 401

    def test_snapshot_missing_file(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "SNAPSHOT_JSON", tmp_path / "absent.json")
        resp = client.get("/api/admin/portfolio/snapshot", headers=_auth())
        assert resp.status_code == 404
        assert resp.get_json()["available"] is False

    def test_snapshot_served_with_auth(self, client, tmp_path, monkeypatch):
        payload = {
            "generated_at": "2026-10-18T00:00:00+00:00",
            "as_of": "2026-10-16", "mode": "paper",
            "model": {"equity": {"dates": [], "values": []}, "metrics": {}},
            "live": {"equity": {"dates": [], "values": []}, "metrics": {}, "positions": []},
            "proposal": None, "adherence": {}, "fills": [],
        }
        path = tmp_path / "portfolio_snapshot.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(server, "SNAPSHOT_JSON", path)
        resp = client.get("/api/admin/portfolio/snapshot", headers=_auth())
        assert resp.status_code == 200
        assert resp.headers["Cache-Control"] == "no-store"
        assert resp.get_json()["mode"] == "paper"

    def test_snapshot_not_in_public_static(self, client):
        assert client.get("/private/portfolio_snapshot.json").status_code == 404

    def test_portfolio_page_loads_live_script(self, client):
        resp = client.get("/admin/portfolio", headers=_auth())
        assert b"/live.js" in resp.data


class TestJournalFills:
    def test_requires_auth(self, client):
        assert client.get("/api/admin/trade/fills").status_code == 401
        assert client.post("/api/admin/trade/fills", json={}).status_code == 401

    def test_create_and_list(self, client):
        resp = client.post(
            "/api/admin/trade/fills",
            headers=_auth(),
            json={
                "trade_date": "2026-10-16",
                "ticker": "spy",
                "side": "BUY",
                "shares": 7,
                "price": 400.5,
                "proposal_id": "2026-10-16-core-buy",
                "note": "filled at open",
            },
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["ok"] is True
        assert body["id"] == 1
        assert body["fill"]["ticker"] == "SPY"  # normalised to upper
        assert body["fill"]["side"] == "buy"
        assert body["fill"]["lane"] == "core"  # defaulted
        assert body["fill"]["source"] == "paper"  # defaulted
        assert body["fill"]["created_at"]

        listed = client.get("/api/admin/trade/fills", headers=_auth()).get_json()
        assert listed["count"] == 1
        assert listed["fills"][0]["price"] == 400.5
        assert listed["fills"][0]["proposal_id"] == "2026-10-16-core-buy"

    def test_created_at_is_server_timestamped(self, client):
        client.post(
            "/api/admin/trade/fills",
            headers=_auth(),
            json={"ticker": "SPY", "side": "buy", "shares": 1, "price": 1.0,
                  "created_at": "1999-01-01T00:00:00+00:00"},
        )
        row = client.get("/api/admin/trade/fills", headers=_auth()).get_json()["fills"][0]
        assert row["created_at"].startswith("20")

    def test_trade_date_defaults_to_today(self, client):
        resp = client.post(
            "/api/admin/trade/fills",
            headers=_auth(),
            json={"ticker": "SPY", "side": "buy", "shares": 1, "price": 1.0},
        )
        assert resp.status_code == 201
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", resp.get_json()["fill"]["trade_date"])

    def test_newest_first(self, client):
        for shares in (1, 2):
            client.post(
                "/api/admin/trade/fills",
                headers=_auth(),
                json={"ticker": "SPY", "side": "buy", "shares": shares, "price": 1.0},
            )
        fills = client.get("/api/admin/trade/fills", headers=_auth()).get_json()["fills"]
        assert [f["shares"] for f in fills] == [2, 1]

    @pytest.mark.parametrize(
        "payload, needle",
        [
            ({"side": "buy", "shares": 1, "price": 1.0}, "ticker"),
            ({"ticker": "SPY", "shares": 1, "price": 1.0}, "side"),
            ({"ticker": "SPY", "side": "hold", "shares": 1, "price": 1.0}, "side"),
            ({"ticker": "SPY", "side": "buy", "shares": 0, "price": 1.0}, "shares"),
            ({"ticker": "SPY", "side": "buy", "shares": -3, "price": 1.0}, "shares"),
            ({"ticker": "SPY", "side": "buy", "shares": "x", "price": 1.0}, "shares"),
            ({"ticker": "SPY", "side": "buy", "shares": 1, "price": 0}, "price"),
            ({"ticker": "SPY", "side": "buy", "shares": 1, "price": "abc"}, "price"),
            ({"ticker": "SPY", "side": "buy", "shares": 1, "price": 1.0,
              "trade_date": "16/10/2026"}, "trade_date"),
            ({"ticker": "SPY", "side": "buy", "shares": 1, "price": 1.0,
              "lane": "hedge"}, "lane"),
            ({"ticker": "SPY", "side": "buy", "shares": 1, "price": 1.0,
              "source": "margin"}, "source"),
        ],
    )
    def test_validation_rejects(self, client, payload, needle):
        resp = client.post("/api/admin/trade/fills", headers=_auth(), json=payload)
        assert resp.status_code == 400
        assert needle in resp.get_json()["error"]


class TestJournalDecisions:
    def test_requires_auth(self, client):
        assert client.get("/api/admin/trade/decisions").status_code == 401
        assert client.post("/api/admin/trade/decisions", json={}).status_code == 401

    def test_create_and_list(self, client):
        resp = client.post(
            "/api/admin/trade/decisions",
            headers=_auth(),
            json={
                "action": "ACTED",
                "trade_date": "2026-10-16",
                "proposal_id": "2026-10-16-core-buy",
                "ticker": "SPY",
                "side": "buy",
                "planned_shares": 7,
                "planned_price": 400.0,
            },
        )
        assert resp.status_code == 201
        decision = resp.get_json()["decision"]
        assert decision["action"] == "acted"
        assert decision["lane"] == "core"
        assert decision["planned_shares"] == 7

        listed = client.get("/api/admin/trade/decisions", headers=_auth()).get_json()
        assert listed["count"] == 1

    def test_planned_fields_default_zero(self, client):
        resp = client.post(
            "/api/admin/trade/decisions",
            headers=_auth(),
            json={"action": "skipped", "ticker": "SPY", "side": "buy"},
        )
        assert resp.status_code == 201
        decision = resp.get_json()["decision"]
        assert decision["planned_shares"] == 0
        assert decision["planned_price"] == 0

    @pytest.mark.parametrize(
        "payload, needle",
        [
            ({"ticker": "SPY", "side": "buy"}, "action"),
            ({"action": "maybe", "ticker": "SPY", "side": "buy"}, "action"),
            ({"action": "acted", "side": "buy"}, "ticker"),
            ({"action": "acted", "ticker": "SPY"}, "side"),
            ({"action": "acted", "ticker": "SPY", "side": "buy",
              "planned_shares": -1}, "planned_shares"),
            ({"action": "acted", "ticker": "SPY", "side": "buy",
              "planned_price": -5}, "planned_price"),
        ],
    )
    def test_validation_rejects(self, client, payload, needle):
        resp = client.post("/api/admin/trade/decisions", headers=_auth(), json=payload)
        assert resp.status_code == 400
        assert needle in resp.get_json()["error"]


class TestJournalExport:
    def test_requires_auth(self, client):
        assert client.get("/api/admin/trade/export/fills.csv").status_code == 401

    def test_fills_csv(self, client):
        client.post(
            "/api/admin/trade/fills",
            headers=_auth(),
            json={"trade_date": "2026-10-16", "ticker": "SPY", "side": "buy",
                  "shares": 7, "price": 400.5},
        )
        resp = client.get("/api/admin/trade/export/fills.csv", headers=_auth())
        assert resp.status_code == 200
        assert resp.mimetype == "text/csv"
        assert "journal_fills.csv" in resp.headers["Content-Disposition"]
        text = resp.data.decode("utf-8").lstrip("\ufeff")
        rows = list(csv.DictReader(text.splitlines()))
        assert rows[0]["ticker"] == "SPY"
        assert rows[0]["shares"] == "7"

    def test_decisions_csv(self, client):
        client.post(
            "/api/admin/trade/decisions",
            headers=_auth(),
            json={"action": "acted", "ticker": "SPY", "side": "buy",
                  "planned_shares": 7},
        )
        resp = client.get("/api/admin/trade/export/decisions.csv", headers=_auth())
        assert resp.status_code == 200
        rows = list(csv.DictReader(resp.data.decode("utf-8").lstrip("\ufeff").splitlines()))
        assert rows[0]["action"] == "acted"

    def test_unknown_export_404(self, client):
        assert client.get("/api/admin/trade/export/nope.csv", headers=_auth()).status_code == 404
