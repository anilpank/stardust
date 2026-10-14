"""Generate an ADMIN_PASS_HASH value for the landing-page admin console.

The hash is ``salt:hash`` where ``salt`` is a random hex string and ``hash`` is
scrypt(password, salt) as hex. Parameters match Node's ``crypto.scryptSync``
defaults (N=16384, r=8, p=1, keylen=64), so a hash produced here (or by the
experiments repo's ``gen-admin-password.js``) works interchangeably.

Usage:
    python landing/gen_admin_password.py 'your-password'
    # or set PASSWORD in the environment
    PASSWORD='your-password' python landing/gen_admin_password.py

Then start the server (or your systemd unit) with:
    ADMIN_PASS_HASH=salt:hash gunicorn ... server:app
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sys

N, R, P, DKLEN = 16384, 8, 1, 64


def make_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    derived = hashlib.scrypt(password.encode(), salt=salt.encode(), n=N, r=R, p=P, dklen=DKLEN)
    return f"{salt}:{derived.hex()}"


def verify_hash(password: str, value: str) -> bool:
    salt, _, expected = value.partition(":")
    if not salt or not expected:
        return False
    try:
        expected_bytes = bytes.fromhex(expected)
    except ValueError:
        return False
    derived = hashlib.scrypt(password.encode(), salt=salt.encode(), n=N, r=R, p=P, dklen=len(expected_bytes))
    return secrets.compare_digest(derived, expected_bytes)


def main(argv: list[str]) -> int:
    password = (argv[1] if len(argv) > 1 else "") or os.environ.get("PASSWORD", "")
    if not password:
        print("Usage: python landing/gen_admin_password.py <password>", file=sys.stderr)
        return 1
    print(f"ADMIN_PASS_HASH={make_hash(password)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
