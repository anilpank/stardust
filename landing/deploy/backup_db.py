#!/usr/bin/env python3
"""Snapshot the landing SQLite database and prune old snapshots.

Runs on the server (via a systemd timer). Uses ``VACUUM INTO`` so the snapshot
is a consistent, compact copy even while gunicorn is writing.

Environment:
    LANDING_DATA_DIR  directory holding landing.db (default: ../data)
    BACKUP_DIR        where snapshots go        (default: ../backups)
    BACKUP_KEEP       how many snapshots to keep (default: 14)
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    here = Path(__file__).resolve().parent
    data_dir = Path(os.environ.get("LANDING_DATA_DIR", here.parent / "data"))
    db_path = data_dir / "landing.db"
    backup_dir = Path(os.environ.get("BACKUP_DIR", here.parent / "backups"))
    keep = int(os.environ.get("BACKUP_KEEP", "14"))

    if not db_path.exists():
        print(f"no database at {db_path}; nothing to back up", file=sys.stderr)
        return 0

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"landing-{stamp}.db"

    src = sqlite3.connect(str(db_path))
    try:
        src.execute("VACUUM INTO ?", (str(dest),))
    finally:
        src.close()

    snapshots = sorted(backup_dir.glob("landing-*.db"))
    for old in snapshots[:-keep]:
        old.unlink()
        print(f"pruned {old.name}")

    print(f"wrote {dest} ({dest.stat().st_size} bytes); kept {min(len(snapshots), keep)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
