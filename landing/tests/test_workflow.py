"""Tests for the daily demo-data refresh workflow.

The GitHub Actions workflow is declarative config, so — as with the deploy
shell scripts in ``test_deploy.py`` — these are invariant checks over its text.
They guard the contract the pipeline relies on: a daily schedule, a strict
price update, a full-universe regeneration from the committed signals, the
landing test gate, and a change-gated PR that commits only the JSON.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "refresh-demo-data.yml"


@pytest.fixture(scope="module")
def text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


class TestRefreshWorkflow:
    def test_exists(self):
        assert WORKFLOW.is_file()

    def test_runs_daily_and_on_demand(self, text):
        assert "cron:" in text
        assert "0 3 * * *" in text
        assert "workflow_dispatch:" in text

    def test_can_push_a_branch_and_open_a_pr(self, text):
        assert "contents: write" in text
        assert "pull-requests: write" in text
        assert "gh pr create" in text

    def test_refreshes_prices_strictly(self, text):
        assert "update_data.py --strict" in text

    def test_regenerates_full_universe_from_committed_signals(self, text):
        assert "generate_demo_data.py" in text
        assert "landing/demo_signals.json" in text
        assert "--tickers" in text
        assert "--workers" in text

    def test_gates_on_landing_tests(self, text):
        assert "pytest landing/tests/" in text

    def test_pr_is_gated_on_a_content_change(self, text):
        assert "generated_at" in text
        assert "steps.changed.outputs.changed == 'true'" in text

    def test_commits_only_the_json_not_parquet(self, text):
        assert "git add landing/demo_signals.json landing/demo_trades.json" in text
        assert ".parquet" not in text
