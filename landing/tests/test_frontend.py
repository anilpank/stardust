"""Static-asset consistency tests.

The landing page is deliberately dependency-light and ships no JS test runner, so
these tests parse the static assets as text and guard the couplings that break
silently: every strategy the generator can emit must have a plain-English
explanation in ``app.js``, every out-of-sample state must be handled, and the
frontend's fallback trade columns must match the generator's.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from landing import generate_demo_data as gen

BASE = Path(__file__).resolve().parent.parent
APP_JS = (BASE / "app.js").read_text(encoding="utf-8")
SIGNALS_JSON = BASE / "demo_signals.json"


def _js_object_keys(marker: str) -> set[str]:
    """Keys of a JS object literal beginning at ``marker`` (e.g. 'var X = {')."""
    block = APP_JS.split(marker, 1)[1].split("};", 1)[0]
    return set(re.findall(r'"([^"]+)"\s*:\s*\{', block))


def _emitted_strategy_labels() -> set[str]:
    payload = json.loads(SIGNALS_JSON.read_text(encoding="utf-8"))
    labels = set()
    for entry in payload["tickers"].values():
        for cell in entry["windows"].values():
            if cell.get("strategy"):
                labels.add(cell["strategy"])
    return labels


def _frontend_trade_columns() -> list[str]:
    block = APP_JS.split("var TRADE_COLUMNS = [", 1)[1].split("];", 1)[0]
    return re.findall(r'"([^"]+)"', block)


class TestStrategyGlossary:
    def test_every_emitted_strategy_has_an_explanation(self):
        if not SIGNALS_JSON.exists():
            pytest.skip("demo_signals.json not present")
        labels = _emitted_strategy_labels()
        assert labels, "expected at least one strategy label in demo_signals.json"
        # Every label shown to a visitor has a glossary/tooltip entry, and there
        # are no stale entries for strategies the engine no longer recommends.
        assert labels == _js_object_keys("var STRATEGY_INFO = {")

    def test_all_oos_states_are_handled(self):
        assert _js_object_keys("var OOS_INFO = {") == {"true", "false", "null"}


class TestTradeColumns:
    def test_frontend_fallback_matches_generator(self):
        assert _frontend_trade_columns() == gen.TRADE_COLUMNS


class TestConsensusPresentation:
    def test_mixed_state_is_styled(self):
        css = (BASE / "styles.css").read_text(encoding="utf-8")
        assert ".signal-MIXED" in css

    def test_app_renders_consensus_pill(self):
        assert "function consensusPill" in APP_JS
        assert "windows agree" in APP_JS

    def test_emitted_signal_is_a_consensus(self):
        if not SIGNALS_JSON.exists():
            pytest.skip("demo_signals.json not present")
        payload = json.loads(SIGNALS_JSON.read_text(encoding="utf-8"))
        for ticker, entry in payload["tickers"].items():
            sig = entry["signal"]
            assert set(sig) == {"direction", "agree", "total", "consensus", "as_of"}, ticker
            assert sig["consensus"] == (sig["direction"] is not None), ticker
            assert sig["total"] >= sig["agree"] >= 0, ticker
            winners = [c for c in entry["windows"].values() if c.get("strategy")]
            assert sig["total"] == len(winners), ticker

    def test_winning_windows_expose_a_call(self):
        if not SIGNALS_JSON.exists():
            pytest.skip("demo_signals.json not present")
        payload = json.loads(SIGNALS_JSON.read_text(encoding="utf-8"))
        for ticker, entry in payload["tickers"].items():
            for cell in entry["windows"].values():
                if cell.get("strategy"):
                    assert "call" in cell, ticker


class TestDemoJsonIntegrity:
    def test_committed_demo_json_is_strict(self):
        for name in ("demo_signals.json", "demo_trades.json"):
            path = BASE / name
            if not path.exists():
                pytest.skip(f"{name} not present")
            payload = json.loads(path.read_text(encoding="utf-8"))
            # allow_nan=False raises if any bare NaN/Infinity slipped in — the
            # browser's JSON.parse rejects those, so they must never ship.
            json.dumps(payload, allow_nan=False)


class TestSignupOpensPrototype:
    def _success_path(self) -> str:
        return APP_JS.split('track("email_submit"', 1)[1].split(".catch(", 1)[0]

    def test_signup_opens_the_ticker_detail(self):
        # The detail must open on every successful signup (not just the first
        # unlock), so the lookup can't live inside the signalsUnlocked() gate.
        assert "lookup(reveal)" in self._success_path()
        assert "#demo-ticker" in self._success_path()

    def test_signup_jumps_to_the_prototype(self):
        assert "function jumpToPrototype" in APP_JS
        assert "jumpToPrototype(" in self._success_path()
        assert 'scrollIntoView({ behavior: "smooth", block: "start" })' in APP_JS

    def test_no_ticker_focuses_the_prototype_input(self):
        # With no ticker there's no detail to open, so land on the prototype and
        # focus its input instead.
        assert "jumpToPrototype(!reveal)" in self._success_path()


class TestGlossaryMount:
    def test_index_has_glossary_mount(self):
        assert 'id="glossary-body"' in (BASE / "index.html").read_text(encoding="utf-8")

    def test_app_renders_glossary_on_load(self):
        assert "renderGlossary()" in APP_JS
