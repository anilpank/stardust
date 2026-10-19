"""Tests for landing/shadow_run.py — the Phase B shadow-loop orchestrator."""

from __future__ import annotations

import subprocess

import pytest

from landing import shadow_run as sr


class FakeRunner:
    def __init__(self, branch="feature/data", staged=False, fail_on=None):
        self.calls = []
        self.branch = branch
        self.staged = staged
        self.fail_on = fail_on or ()

    def __call__(self, cmd, cwd):
        self.calls.append(list(cmd))
        cp = subprocess.CompletedProcess(cmd, 0, "", "")
        if tuple(cmd[:3]) == ("git", "rev-parse", "--abbrev-ref"):
            cp.stdout = self.branch + "\n"
        if tuple(cmd[:4]) == ("git", "diff", "--cached", "--quiet"):
            cp.returncode = 1 if self.staged else 0
        if any(cmd[:len(f)] == list(f) for f in self.fail_on):
            cp.returncode = 2
            cp.stderr = "boom"
        return cp

    def verbs(self):
        return [c[0] for c in self.calls]

    def git(self):
        return [c for c in self.calls if c and c[0] == "git"]


def test_build_commands_runs_data_then_snapshot():
    cmds = sr.build_commands("py")
    assert cmds[0] == ["py", "thermaltrend/update_data.py"]
    assert cmds[1][:3] == ["py", "landing/generate_portfolio_snapshot.py", "--mode"]
    assert cmds[1][3] == "paper"


def test_build_commands_skip_data():
    cmds = sr.build_commands("py", skip_data=True)
    assert len(cmds) == 1
    assert "update_data.py" not in " ".join(cmds[0])


def test_build_commands_forwards_flags():
    cmds = sr.build_commands(
        "py", mode="live", data_dir="/d", out="/o", config="/c",
        base_url="http://h", journal_dir="/j", user="u", password="p",
        start="2020-01-01", end="2021-01-01",
    )
    snap = cmds[-1]
    joined = " ".join(snap)
    for frag in ["--mode live", "--data-dir /d", "--out /o", "--config /c",
                 "--base-url http://h", "--journal-dir /j", "--user u",
                 "--password p", "--start 2020-01-01", "--end 2021-01-01"]:
        assert frag in joined


def test_run_loop_without_commit_runs_no_git():
    runner = FakeRunner()
    result = sr.run_loop(python="py", runner=runner)
    assert result["committed"] is False
    assert runner.git() == []
    assert runner.verbs() == ["py", "py"]


def test_run_loop_commit_on_feature_branch():
    runner = FakeRunner(branch="feat/data-refresh", staged=True)
    result = sr.run_loop(python="py", commit=True, runner=runner)
    assert result["committed"] is True
    assert result["branch"] == "feat/data-refresh"
    assert result["message"].startswith("chore: shadow-run update")
    git = runner.git()
    assert git[0][:3] == ["git", "rev-parse", "--abbrev-ref"]
    assert git[1][:2] == ["git", "add"]
    assert "thermaltrend/data" in git[1]
    assert "landing/private/portfolio_snapshot.json" in git[1]
    assert git[2] == ["git", "diff", "--cached", "--quiet"]
    assert git[3][:2] == ["git", "commit"]


def test_run_loop_commit_refuses_main():
    runner = FakeRunner(branch="main", staged=True)
    with pytest.raises(RuntimeError):
        sr.run_loop(python="py", commit=True, runner=runner)
    assert [c for c in runner.git() if c[:2] == ["git", "add"]] == []


def test_run_loop_commit_allows_main_with_flag():
    runner = FakeRunner(branch="main", staged=True)
    result = sr.run_loop(python="py", commit=True, allow_main=True, runner=runner)
    assert result["committed"] is True


def test_run_loop_commit_no_changes():
    runner = FakeRunner(branch="feat/x", staged=False)
    result = sr.run_loop(python="py", commit=True, runner=runner)
    assert result["committed"] is False
    assert [c for c in runner.git() if c[:2] == ["git", "commit"]] == []


def test_run_loop_custom_message():
    runner = FakeRunner(staged=True)
    result = sr.run_loop(python="py", commit=True, message="chore: data", runner=runner)
    assert result["message"] == "chore: data"
    assert ["git", "commit", "-m", "chore: data"] in runner.git()


def test_run_loop_propagates_failure():
    runner = FakeRunner(fail_on=[("py", "thermaltrend/update_data.py")])
    with pytest.raises(RuntimeError):
        sr.run_loop(python="py", runner=runner)
