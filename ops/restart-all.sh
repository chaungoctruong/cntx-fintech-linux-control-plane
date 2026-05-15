#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_PATH="${ENV_PATH:-.env}"
READY_URL="${READY_URL:-http://127.0.0.1:8001/ready}"

log() { printf '[restart-all] %s\n' "$*"; }
die() { printf '[restart-all] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "${1:-}" == "--yes" ]] || die "this restarts db/redis/hubbot/spider-app/token-bot; rerun: ops/restart-all.sh --yes"
[[ -f "$ENV_PATH" ]] || die "$ENV_PATH not found"
command -v docker >/dev/null 2>&1 || die "docker is not installed"
docker compose version >/dev/null 2>&1 || die "docker compose is not available"
command -v curl >/dev/null 2>&1 || die "curl is not installed"

export ENV_FILE="$ENV_PATH"
COMPOSE=(docker compose --env-file "$ENV_PATH")

wait_service() {
  local service="$1"
  local expected="$2"
  local cid state
  cid="$("${COMPOSE[@]}" ps -q "$service")"
  [[ -n "$cid" ]] || die "$service container not found"
  for _ in $(seq 1 60); do
    state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid")"
    if [[ "$state" == "$expected" ]]; then
      log "$service $expected"
      return 0
    fi
    sleep 2
  done
  die "$service did not become $expected"
}

log "current status"
"${COMPOSE[@]}" ps

log "restarting infrastructure: db redis"
"${COMPOSE[@]}" restart db redis
wait_service db healthy
wait_service redis healthy

log "restarting application services: hubbot spider-app token-bot"
"${COMPOSE[@]}" restart hubbot spider-app token-bot-api token-bot-tg
wait_service hubbot running
wait_service spider-app healthy
wait_service token-bot-api healthy
wait_service token-bot-tg running

log "checking backend readiness: $READY_URL"
curl -fsS -m 5 "$READY_URL" >/dev/null || die "backend /ready failed"
log "ready OK"

log "final status"
"${COMPOSE[@]}" ps

log "DONE"
