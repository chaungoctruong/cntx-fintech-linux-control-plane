#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

set -a
# shellcheck disable=SC1091
source .env
set +a

BACKUP_DIR="ops/artifacts/backups/postgres"
mkdir -p "$BACKUP_DIR"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/${POSTGRES_DB:-spider_ai_saas}_${STAMP}.sql.gz"

docker compose exec -T \
  -e PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD missing}" \
  db pg_dump \
  -U "${POSTGRES_USER:?POSTGRES_USER missing}" \
  -d "${POSTGRES_DB:?POSTGRES_DB missing}" \
  --no-owner \
  --no-acl \
  | gzip -9 > "$OUT"

chmod 600 "$OUT"
printf 'Postgres backup created: %s\n' "$OUT"
