"""Run the Phase B shadow loop: refresh prices, regenerate the snapshot, commit.

Orchestrates `thermaltrend/update_data.py` then
`landing/generate_portfolio_snapshot.py`, and (opt-in) commits the refreshed
data + snapshot on the current branch. Never pushes. Refuses to commit on
`main`/`master` unless `--allow-main` is passed, so the operator reviews and
merges as usual.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
DEFAULT_DATA_DIR = REPO_ROOT / "thermaltrend" / "data" / "equities"
DEFAULT_OUT = BASE_DIR / "private" / "portfolio_snapshot.json"
LIVE_CONFIG_PATH = REPO_ROOT / "thermaltrend" / "portfolio" / "live_config.json"
DATA_PATHS = ("thermaltrend/data", "landing/private/portfolio_snapshot.json")
PROTECTED_BRANCHES = ("main", "master")


def _runner(cmd, cwd):
    return subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True)


def _must(proc, cmd):
    if proc.returncode != 0:
        raise RuntimeError(
            "command failed (%d): %s\n%s%s"
            % (proc.returncode, " ".join(cmd), proc.stdout or "", proc.stderr or "")
        )
    return proc


def build_commands(
    python: str,
    *,
    mode: str = "paper",
    data_dir=None,
    out=None,
    config=None,
    base_url=None,
    journal_dir=None,
    user=None,
    password=None,
    start=None,
    end=None,
    skip_data: bool = False,
):
    cmds = []
    if not skip_data:
        cmds.append([python, "thermaltrend/update_data.py"])
    snap = [python, "landing/generate_portfolio_snapshot.py", "--mode", mode]
    if data_dir:
        snap += ["--data-dir", str(data_dir)]
    if out:
        snap += ["--out", str(out)]
    if config:
        snap += ["--config", str(config)]
    if base_url:
        snap += ["--base-url", base_url]
    if journal_dir:
        snap += ["--journal-dir", str(journal_dir)]
    if user:
        snap += ["--user", user]
    if password:
        snap += ["--password", password]
    if start:
        snap += ["--start", start]
    if end:
        snap += ["--end", end]
    cmds.append(snap)
    return cmds


def run_loop(
    *,
    python: str | None = None,
    mode: str = "paper",
    data_dir=None,
    out=None,
    config=None,
    base_url=None,
    journal_dir=None,
    user=None,
    password=None,
    start=None,
    end=None,
    skip_data: bool = False,
    commit: bool = False,
    allow_main: bool = False,
    message: str | None = None,
    runner=_runner,
    root: Path = REPO_ROOT,
):
    python = python or sys.executable
    commands = build_commands(
        python, mode=mode, data_dir=data_dir, out=out, config=config,
        base_url=base_url, journal_dir=journal_dir, user=user, password=password,
        start=start, end=end, skip_data=skip_data,
    )
    for cmd in commands:
        _must(runner(cmd, root), cmd)

    result = {"commands": commands, "committed": False, "branch": None, "message": None}
    if not commit:
        return result

    branch = _must(runner(["git", "rev-parse", "--abbrev-ref", "HEAD"], root),
                   ["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    result["branch"] = branch
    if branch in PROTECTED_BRANCHES and not allow_main:
        raise RuntimeError(
            "refusing to commit on %r; switch to a data branch or pass --allow-main" % branch
        )

    _must(runner(["git", "add", *DATA_PATHS], root), ["git", "add", *DATA_PATHS])
    staged = runner(["git", "diff", "--cached", "--quiet"], root)
    if staged.returncode == 0:
        return result
    msg = message or "chore: shadow-run update %s" % _dt.date.today().isoformat()
    _must(runner(["git", "commit", "-m", msg], root), ["git", "commit", "-m", msg])
    result["committed"] = True
    result["message"] = msg
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the Phase B shadow loop")
    parser.add_argument("--mode", choices=("paper", "live"), default="paper")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--config", default=str(LIVE_CONFIG_PATH))
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--journal-dir", default=None)
    parser.add_argument("--user", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--skip-data", action="store_true",
                        help="skip update_data.py and only rebuild the snapshot")
    parser.add_argument("--commit", action="store_true",
                        help="commit refreshed data + snapshot on the current branch")
    parser.add_argument("--allow-main", action="store_true",
                        help="permit --commit on main/master (default: refuse)")
    parser.add_argument("--message", default=None)
    args = parser.parse_args(argv)
    try:
        result = run_loop(
            mode=args.mode, data_dir=args.data_dir, out=args.out, config=args.config,
            base_url=args.base_url, journal_dir=args.journal_dir, user=args.user,
            password=args.password, start=args.start, end=args.end,
            skip_data=args.skip_data, commit=args.commit, allow_main=args.allow_main,
            message=args.message,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for cmd in result["commands"]:
        print("+", " ".join(cmd))
    if result["committed"]:
        print("committed on %s: %s" % (result["branch"], result["message"]))
    elif args.commit:
        print("no changes to commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
