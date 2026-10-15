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


class TestGlossaryMount:
    def test_index_has_glossary_mount(self):
        assert 'id="glossary-body"' in (BASE / "index.html").read_text(encoding="utf-8")

    def test_app_renders_glossary_on_load(self):
        assert "renderGlossary()" in APP_JS
