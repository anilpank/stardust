#!/usr/bin/env bash
set -euo pipefail

# Pull a consistent snapshot of the landing database from the server to your
# laptop (uses the admin Download DB endpoint).
#
# Usage:
#   ADMIN_PASSWORD='...' bash pull-db.sh [out.db]
#   DOMAIN=stardust.ojblabs.com ADMIN_USER=admin ADMIN_PASSWORD='...' bash pull-db.sh

DOMAIN="${DOMAIN:-stardust.ojblabs.com}"
ADMIN_USER="${ADMIN_USER:-admin}"
OUT="${1:-landing-$(date -u +%Y%m%d-%H%M%S).db}"

: "${ADMIN_PASSWORD:?set ADMIN_PASSWORD (the admin console password)}"

curl -fsS -u "$ADMIN_USER:$ADMIN_PASSWORD" -o "$OUT" "https://$DOMAIN/api/admin/db"
echo "wrote $OUT ($(wc -c <"$OUT" | tr -d ' ') bytes)"
