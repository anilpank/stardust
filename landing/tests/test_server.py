"""Tests for landing/server.py — the experiment backend.

Unit tests cover the pure helpers (CSV parsing, summary computation). The
integration tests drive the real Flask app with its test client and assert on
the CSVs it writes, so the full signup / tracking / admin funnel is exercised
end to end with no network and no real lead data.
"""

from __future__ import annotations

import csv
import json

import pytest

from landing import server


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


class TestReadCsv:
    def test_missing_file_returns_empty(self, tmp_path):
        assert server._read_csv(tmp_path / "nope.csv") == []

    def test_reads_rows(self, tmp_path):
        path = tmp_path / "x.csv"
        path.write_text("a,b\n1,2\n3,4\n")
        assert server._read_csv(path) == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]


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


# --------------------------------------------------------------------------- #
# Integration — Flask test client
# --------------------------------------------------------------------------- #
PIN = "9999"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "SIGNUPS_CSV", tmp_path / "signups.csv")
    monkeypatch.setattr(server, "EVENTS_CSV", tmp_path / "events.csv")
    monkeypatch.setattr(server, "ADMIN_PIN", PIN)
    server.app.config.update(TESTING=True)
    with server.app.test_client() as c:
        yield c


def _rows(path):
    assert path.exists(), f"{path} was not created"
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


class TestStaticRoutes:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_index_served(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Stop guessing" in resp.data

    def test_admin_page_served(self, client):
        resp = client.get("/admin")
        assert resp.status_code == 200
        assert b"experiment console" in resp.data

    def test_static_asset_served(self, client):
        assert client.get("/styles.css").status_code == 200

    def test_unknown_file_404(self, client):
        assert client.get("/does-not-exist.css").status_code == 404

    def test_unknown_api_path_404(self, client):
        assert client.get("/api/nope").status_code == 404


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

        rows = _rows(server.SIGNUPS_CSV)
        assert len(rows) == 1
        assert rows[0]["email"] == "person@example.com"  # normalised
        assert rows[0]["ticker"] == "AAPL"               # upper-cased
        assert rows[0]["source"] == "reddit_algotrading"
        assert rows[0]["is_return"] == "false"

    @pytest.mark.parametrize("email", ["", "not-an-email", "a@b", "no@dot@x.com"])
    def test_invalid_email_rejected(self, client, email):
        resp = client.post("/api/signup", json={"email": email, "ticker": "AAPL"})
        assert resp.status_code == 400
        assert not server.SIGNUPS_CSV.exists()

    def test_invalid_ticker_rejected(self, client):
        resp = client.post("/api/signup", json={"email": "a@b.com", "ticker": "TOOLONGTICKER"})
        assert resp.status_code == 400

    def test_ticker_optional(self, client):
        resp = client.post("/api/signup", json={"email": "a@b.com"})
        assert resp.status_code == 200
        assert _rows(server.SIGNUPS_CSV)[0]["ticker"] == ""


class TestTrack:
    def test_valid_event_written(self, client):
        resp = client.post("/api/track", json={
            "event": "ticker_lookup", "ticker": "aapl", "source": "hn",
            "visitor_id": "v1", "meta": {"known": True},
        })
        assert resp.status_code == 200
        rows = _rows(server.EVENTS_CSV)
        assert rows[0]["event"] == "ticker_lookup"
        assert rows[0]["ticker"] == "AAPL"
        assert json.loads(rows[0]["meta"]) == {"known": True}

    def test_event_required(self, client):
        assert client.post("/api/track", json={"ticker": "AAPL"}).status_code == 400


class TestAdminAuth:
    def test_data_requires_login(self, client):
        assert client.get("/api/admin/data").status_code == 401

    def test_wrong_pin_rejected(self, client):
        assert client.post("/api/admin/login", json={"pin": "0000"}).status_code == 401

    def test_login_then_read(self, client):
        assert client.post("/api/admin/login", json={"pin": PIN}).status_code == 200
        body = client.get("/api/admin/data").get_json()
        assert body["ok"] is True
        assert "summary" in body and "signups" in body and "events" in body

    def test_logout_revokes(self, client):
        client.post("/api/admin/login", json={"pin": PIN})
        assert client.post("/api/admin/logout").status_code == 200
        assert client.get("/api/admin/data").status_code == 401

    def test_export_guarded(self, client):
        assert client.get("/api/admin/export/signups.csv").status_code == 401

    def test_export_after_login(self, client):
        client.post("/api/signup", json={"email": "a@b.com", "ticker": "AAPL"})
        client.post("/api/admin/login", json={"pin": PIN})
        resp = client.get("/api/admin/export/signups.csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["Content-Type"]
        assert b"a@b.com" in resp.data

    def test_export_unknown_name_404(self, client):
        client.post("/api/admin/login", json={"pin": PIN})
        assert client.get("/api/admin/export/whatever.csv").status_code == 404


class TestEndToEndFunnel:
    def test_full_funnel_reflected_in_admin(self, client):
        # A visitor arrives from Reddit and moves through the funnel.
        client.post("/api/track", json={"event": "visit", "visitor_id": "v1", "source": "reddit"})
        client.post("/api/track", json={"event": "ticker_lookup", "visitor_id": "v1", "ticker": "AAPL", "source": "reddit"})
        client.post("/api/track", json={"event": "signal_view", "visitor_id": "v1", "ticker": "AAPL", "source": "reddit"})
        client.post("/api/signup", json={"email": "lead@example.com", "ticker": "AAPL", "visitor_id": "v1", "source": "reddit"})
        client.post("/api/track", json={"event": "email_submit", "visitor_id": "v1", "ticker": "AAPL", "source": "reddit"})
        client.post("/api/track", json={"event": "wtp_yes", "visitor_id": "v1", "source": "reddit"})

        client.post("/api/admin/login", json={"pin": PIN})
        data = client.get("/api/admin/data").get_json()

        assert data["summary"]["kpis"]["leads"] == 1
        assert data["summary"]["kpis"]["conversion_pct"] == 100.0
        assert data["summary"]["kpis"]["wtp_yes"] == 1
        assert data["summary"]["top_tickers"][0]["ticker"] == "AAPL"
        assert data["signups"][0]["email"] == "lead@example.com"
        # newest-first ordering
        assert data["events"][0]["event"] == "wtp_yes"
