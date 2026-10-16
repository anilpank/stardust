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


def _js_block(marker: str) -> str:
    """Body of a JS object literal beginning at ``marker`` (through its closing ``};``)."""
    return APP_JS.split(marker, 1)[1].split("};", 1)[0]


def _index_prose() -> str:
    """index.html with HTML comments stripped, so we test what a visitor reads."""
    html = (BASE / "index.html").read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


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

    def test_every_strategy_has_an_example(self):
        block = _js_block("var STRATEGY_INFO = {")
        keys = _js_object_keys("var STRATEGY_INFO = {")
        # One plain-English example per strategy, so every glossary card teaches
        # the rule with a concrete scenario instead of bare jargon.
        assert block.count("example:") == len(keys)
        assert block.count("blurb:") == len(keys)


class TestPlainEnglishCopy:
    def test_explanations_avoid_em_dash(self):
        # An em-dash is the tell that copy was machine-written; keep it out of
        # the strategy and honesty-badge explanations visitors read.
        assert "—" not in _js_block("var STRATEGY_INFO = {")
        assert "—" not in _js_block("var OOS_INFO = {")

    def test_index_prose_avoids_em_dash(self):
        assert "—" not in _index_prose()

    def test_why_different_leads_with_out_of_sample(self):
        prose = _index_prose()
        assert "whether it still works" in prose
        # The re-tested-on-unseen-data angle comes before the survivorship card.
        assert prose.index("second opinion") < prose.index("kicked out")


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
            assert set(sig) == {
                "direction", "agree", "total", "consensus", "as_of",
                "since", "entry_price", "return_pct",
            }, ticker
            assert sig["consensus"] == (sig["direction"] is not None), ticker
            assert sig["total"] >= sig["agree"] >= 0, ticker
            winners = [c for c in entry["windows"].values() if c.get("strategy")]
            assert sig["total"] == len(winners), ticker

    def test_emitted_price_block_has_a_close(self):
        if not SIGNALS_JSON.exists():
            pytest.skip("demo_signals.json not present")
        payload = json.loads(SIGNALS_JSON.read_text(encoding="utf-8"))
        for ticker, entry in payload["tickers"].items():
            price = entry["price"]
            assert set(price) == {"last", "as_of"}, ticker
            assert price["last"] > 0, ticker
            assert price["as_of"], ticker

    def test_winning_windows_expose_a_call(self):
        if not SIGNALS_JSON.exists():
            pytest.skip("demo_signals.json not present")
        payload = json.loads(SIGNALS_JSON.read_text(encoding="utf-8"))
        for ticker, entry in payload["tickers"].items():
            for cell in entry["windows"].values():
                if cell.get("strategy"):
                    assert "call" in cell, ticker
                    assert set(cell["call"]) == {
                        "direction", "as_of", "entry_price", "return_pct",
                    }, ticker


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


class TestPriceContextPresentation:
    def test_app_fetches_a_live_quote(self):
        assert "function loadQuote" in APP_JS
        assert "/api/quote?ticker=" in APP_JS
        assert "data-quote" in APP_JS

    def test_app_renders_signal_since_context(self):
        assert "function signalContext" in APP_JS
        assert "function windowCallContext" in APP_JS
        assert "since" in APP_JS

    def test_date_only_strings_do_not_shift_by_timezone(self):
        # as_of/since are "YYYY-MM-DD"; formatting them with new Date() would
        # render the previous day in negative UTC offsets.
        assert "function fmtDay" in APP_JS
        assert "fmtDay(" in APP_JS

    def test_context_styles_exist(self):
        css = (BASE / "styles.css").read_text(encoding="utf-8")
        for selector in (".signal-context", ".win-call-ctx", ".quote"):
            assert selector in css

