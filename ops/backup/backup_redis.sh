#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

set -a
# shellcheck disable=SC1091
source .env
set +a

BACKUP_DIR="ops/artifacts/backups/redis"
mkdir -p "$BACKUP_DIR"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/redis_${STAMP}.rdb"

docker compose exec -T redis redis-cli -a "${LOCAL_REDIS_PASSWORD:?LOCAL_REDIS_PASSWORD missing}" --no-auth-warning BGSAVE >/dev/null

for _ in $(seq 1 30); do
  if docker compose exec -T redis redis-cli -a "$LOCAL_REDIS_PASSWORD" --no-auth-warning INFO persistence | grep -q 'rdb_bgsave_in_progress:0'; then
    break
  fi
  sleep 1
done

CONTAINER_ID="$(docker compose ps -q redis)"
docker cp "$CONTAINER_ID:/data/dump.rdb" "$OUT"
chmod 600 "$OUT"
printf 'Redis backup created: %s\n' "$OUT"
