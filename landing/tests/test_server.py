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

    def test_unknown_ticker(self, client, demo_file):
        body = client.get("/api/demo?ticker=ZZZZ").get_json()
        assert body["available"] is False
        assert body["data"] is None

    def test_missing_demo_file(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "DEMO_JSON", tmp_path / "missing.json")
        body = client.get("/api/demo?ticker=AAPL").get_json()
        assert body["available"] is False


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
