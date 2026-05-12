#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_PATH="${ENV_PATH:-.env}"
RUNNER_ID="${1:-${RUNNER_ID:-runner-win-test-01}}"
READY_URL="${READY_URL:-http://127.0.0.1:8001/ready}"
BOOTSTRAP_URL="${BOOTSTRAP_URL:-http://127.0.0.1:8001/api/v2/runner/bootstrap?runner_id=${RUNNER_ID}}"

log() { printf '[reload-spider-app] %s\n' "$*"; }
die() { printf '[reload-spider-app] ERROR: %s\n' "$*" >&2; exit 1; }

[[ -f "$ENV_PATH" ]] || die "$ENV_PATH not found"
command -v docker >/dev/null 2>&1 || die "docker is not installed"
docker compose version >/dev/null 2>&1 || die "docker compose is not available"
command -v curl >/dev/null 2>&1 || die "curl is not installed"
command -v python3 >/dev/null 2>&1 || die "python3 is not installed"

export ENV_FILE="$ENV_PATH"

log "building spider-app image from current source"
docker compose --env-file "$ENV_PATH" build spider-app

log "recreating spider-app only; db/redis/hubbot are left untouched"
docker compose --env-file "$ENV_PATH" up -d --no-deps --force-recreate spider-app

log "waiting for backend readiness: $READY_URL"
for _ in $(seq 1 60); do
  if curl -fsS -m 2 "$READY_URL" >/dev/null 2>&1; then
    log "ready OK"
    break
  fi
  sleep 1
done
curl -fsS -m 5 "$READY_URL" >/dev/null || die "backend did not become ready"

BACKEND_API_KEY="${BACKEND_API_KEY:-$(awk -F= '$1=="BACKEND_API_KEY"{v=substr($0,index($0,"=")+1); gsub(/^["'\'' ]+|["'\'' ]+$/,"",v); print v}' "$ENV_PATH" | tail -n 1)}"
[[ -n "$BACKEND_API_KEY" ]] || die "BACKEND_API_KEY missing; cannot verify bootstrap contract"

log "verifying bootstrap contract for runner_id=$RUNNER_ID"
python3 - "$BOOTSTRAP_URL" "$BACKEND_API_KEY" <<'PY'
import json
import sys
import urllib.request

url, api_key = sys.argv[1], sys.argv[2]
req = urllib.request.Request(url, headers={"X-Backend-Api-Key": api_key})
with urllib.request.urlopen(req, timeout=15) as resp:
    payload = json.loads(resp.read().decode("utf-8"))

transport = payload.get("transport") or {}
stop_bot = ((payload.get("contract") or {}).get("stop_bot") or {})
checks = [
    ("transport.recommended", transport.get("recommended") == "redis_queue"),
    ("transport.supported", transport.get("supported") == ["redis_queue"]),
    ("stop_bot.kill_worker", stop_bot.get("kill_worker") is True),
    ("stop_bot.kill_mt5", stop_bot.get("kill_mt5") is True),
    ("stop_bot.terminate_mt5", stop_bot.get("terminate_mt5") is True),
    ("stop_bot.release_terminal", stop_bot.get("release_terminal") is True),
]
failed = [name for name, ok in checks if not ok]
if failed:
    raise SystemExit("bootstrap contract mismatch: " + ", ".join(failed))
print("bootstrap OK")
PY

log "docker status"
docker compose --env-file "$ENV_PATH" ps spider-app

log "DONE"
