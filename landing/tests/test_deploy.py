"""Tests for landing/deploy/ tooling.

``backup_db.py`` is imported directly for unit tests of its snapshot/prune
logic, and also executed as a subprocess to exercise its ``__main__`` entrypoint
and environment plumbing end to end. The ``provision.sh`` / ``pull-db.sh`` shell
scripts are checked for syntax, run against stubbed commands on a temporary
PATH, and asserted against the invariants they rely on (notably the machine-wide
git-config bypass and the isolation guarantees for a co-hosted site).

No network is used: ``curl`` and the other external commands are replaced by
stubs.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from landing.deploy import backup_db

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "landing" / "deploy"
PROVISION = DEPLOY_DIR / "provision.sh"
PULL_DB = DEPLOY_DIR / "pull-db.sh"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _make_db(path: Path, rows: int = 3) -> None:
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE signups (email TEXT)")
        con.executemany(
            "INSERT INTO signups VALUES (?)", [(f"u{i}@example.com",) for i in range(rows)]
        )
        con.commit()
    finally:
        con.close()


def _snapshot_rows(path: Path) -> int:
    con = sqlite3.connect(str(path))
    try:
        return con.execute("SELECT COUNT(*) FROM signups").fetchone()[0]
    finally:
        con.close()


def _stub(tmp_path: Path, name: str, body: str) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / name
    script.write_text(body)
    script.chmod(0o755)
    return bindir


def _with_bin(monkeypatch, bindir: Path) -> None:
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


# --------------------------------------------------------------------------- #
# backup_db.py — unit tests
# --------------------------------------------------------------------------- #
class TestBackupDb:
    def _run(self, monkeypatch, data_dir: Path, backup_dir: Path, keep=None) -> int:
        monkeypatch.setenv("LANDING_DATA_DIR", str(data_dir))
        monkeypatch.setenv("BACKUP_DIR", str(backup_dir))
        if keep is not None:
            monkeypatch.setenv("BACKUP_KEEP", str(keep))
        return backup_db.main()

    def test_missing_db_is_a_noop(self, tmp_path, monkeypatch, capsys):
        data_dir = tmp_path / "data"
        backup_dir = tmp_path / "backups"
        rc = self._run(monkeypatch, data_dir, backup_dir)

        assert rc == 0
        assert "nothing to back up" in capsys.readouterr().err
        assert not backup_dir.exists()

    def test_writes_a_valid_consistent_snapshot(self, tmp_path, monkeypatch, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _make_db(data_dir / "landing.db", rows=5)
        backup_dir = tmp_path / "backups"

        rc = self._run(monkeypatch, data_dir, backup_dir)

        assert rc == 0
        snaps = list(backup_dir.glob("landing-*.db"))
        assert len(snaps) == 1
        assert _snapshot_rows(snaps[0]) == 5
        assert "wrote" in capsys.readouterr().out

    def test_prunes_to_keep_newest(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _make_db(data_dir / "landing.db")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        for stamp in ("20200101-000000", "20200102-000000", "20200103-000000"):
            (backup_dir / f"landing-{stamp}.db").write_bytes(b"old")

        self._run(monkeypatch, data_dir, backup_dir, keep=2)

        kept = list(backup_dir.glob("landing-*.db"))
        assert len(kept) == 2
        # The freshest pre-existing snapshot and the brand-new one survive.
        assert any(p.name == "landing-20200103-000000.db" for p in kept)
        fresh = [p for p in kept if p.read_bytes() != b"old"]
        assert len(fresh) == 1
        assert _snapshot_rows(fresh[0]) == 3

    def test_keep_one_leaves_only_newest(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _make_db(data_dir / "landing.db")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        for stamp in ("20200101-000000", "20200102-000000"):
            (backup_dir / f"landing-{stamp}.db").write_bytes(b"old")

        self._run(monkeypatch, data_dir, backup_dir, keep=1)

        remaining = list(backup_dir.glob("landing-*.db"))
        assert len(remaining) == 1
        assert _snapshot_rows(remaining[0]) == 3  # the fresh snapshot, not a stub


# --------------------------------------------------------------------------- #
# backup_db.py — subprocess integration (real __main__ + env plumbing)
# --------------------------------------------------------------------------- #
class TestBackupDbSubprocess:
    def _env(self, data_dir: Path, backup_dir: Path) -> dict:
        env = dict(os.environ)
        env["LANDING_DATA_DIR"] = str(data_dir)
        env["BACKUP_DIR"] = str(backup_dir)
        return env

    def test_runs_as_a_script(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _make_db(data_dir / "landing.db", rows=2)
        backup_dir = tmp_path / "backups"

        proc = subprocess.run(
            [sys.executable, str(DEPLOY_DIR / "backup_db.py")],
            env=self._env(data_dir, backup_dir),
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 0, proc.stderr
        snaps = list(backup_dir.glob("landing-*.db"))
        assert len(snaps) == 1
        assert _snapshot_rows(snaps[0]) == 2

    def test_no_db_exits_zero(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, str(DEPLOY_DIR / "backup_db.py")],
            env=self._env(tmp_path / "data", tmp_path / "backups"),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0
        assert "nothing to back up" in proc.stderr


# --------------------------------------------------------------------------- #
# shell scripts — syntax
# --------------------------------------------------------------------------- #
class TestShellSyntax:
    @pytest.mark.parametrize("script", [PROVISION, PULL_DB], ids=["provision", "pull-db"])
    def test_bash_syntax_ok(self, script):
        proc = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr

    @pytest.mark.parametrize("script", [PROVISION, PULL_DB], ids=["provision", "pull-db"])
    def test_shellcheck_has_no_errors(self, script):
        shellcheck = shutil.which("shellcheck")
        if shellcheck is None:
            pytest.skip("shellcheck not installed")
        proc = subprocess.run(
            [shellcheck, "--severity=error", "-x", str(script)], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# provision.sh — invariants
# --------------------------------------------------------------------------- #
class TestProvisionInvariants:
    def test_refuses_to_run_as_non_root(self):
        if os.geteuid() == 0:
            pytest.skip("cannot exercise the non-root guard as root")
        proc = subprocess.run(["bash", str(PROVISION)], capture_output=True, text=True)
        assert proc.returncode == 1
        assert "Run as root" in proc.stderr

    def test_bypasses_global_git_config_rewrite(self):
        text = PROVISION.read_text()
        # The EC2 host rewrites GitHub https URLs to ssh via /etc/gitconfig; the
        # clone must ignore that to stay on https (regression guard).
        assert "GIT_CONFIG_NOSYSTEM=1" in text
        assert "GIT_CONFIG_GLOBAL=/dev/null" in text
        assert "GIT_CLEAN" in text

    def test_update_path_is_idempotent(self):
        text = PROVISION.read_text()
        assert '[ -d "$APP_DIR/.git" ]' in text
        assert "reset --hard" in text
        assert "sparse-checkout set landing" in text

    def test_writes_its_own_units_and_is_isolated(self):
        text = PROVISION.read_text()
        assert "SERVICE=stardust-landing" in text
        assert "/etc/systemd/system/$SERVICE.service" in text
        assert "$SERVICE-backup.timer" in text
        assert "/etc/nginx/conf.d/stardust.conf" in text
        assert "server:app" in text
        assert "--workers 2" in text
        # It must never overwrite the host-global nginx config or a co-hosted
        # site's server block.
        assert "cat >/etc/nginx/nginx.conf" not in text
        assert "cat >/etc/nginx/conf.d/ojblabs.conf" not in text

    def test_issues_a_dedicated_cert_for_the_domain(self):
        text = PROVISION.read_text()
        assert "certbot --nginx -d" in text
        assert "/etc/letsencrypt/live/$DOMAIN" in text


# --------------------------------------------------------------------------- #
# pull-db.sh — behaviour with a stubbed curl
# --------------------------------------------------------------------------- #
class TestPullDbScript:
    _STUB = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$CURL_ARGS_FILE"
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    *) shift ;;
  esac
done
if [ -n "$out" ]; then printf 'SQLITE-SNAPSHOT' > "$out"; fi
exit 0
"""

    def test_requires_admin_password(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        proc = subprocess.run(
            ["bash", str(PULL_DB)],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert proc.returncode != 0
        assert "ADMIN_PASSWORD" in proc.stderr

    def test_transfers_snapshot_via_curl(self, monkeypatch, tmp_path):
        bindir = _stub(tmp_path, "curl", self._STUB)
        _with_bin(monkeypatch, bindir)
        args_file = tmp_path / "curl-args.txt"
        monkeypatch.setenv("CURL_ARGS_FILE", str(args_file))
        monkeypatch.setenv("DOMAIN", "example.test")
        monkeypatch.setenv("ADMIN_USER", "admin")
        monkeypatch.setenv("ADMIN_PASSWORD", "s3cret")
        out = tmp_path / "snap.db"

        proc = subprocess.run(
            ["bash", str(PULL_DB), str(out)],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=dict(os.environ),
        )

        assert proc.returncode == 0, proc.stderr
        assert out.read_bytes() == b"SQLITE-SNAPSHOT"
        args = args_file.read_text()
        assert "admin:s3cret" in args
        assert "https://example.test/api/admin/db" in args
