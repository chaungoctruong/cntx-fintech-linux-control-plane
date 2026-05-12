#!/usr/bin/env bash
# Preflight — Linux control-plane (read-only).
# Does not: install packages, write files, restart services, print secret values.
# Usage: from repo root —  bash ops/preflight_linux_control_plane.sh
# Optional: BACKEND_ENV_FILE=/path/to/backend.env (defaults below)

set +e
set +o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 2

PASS=0
WARN=0
FAIL=0

pass() { echo "PASS  $*"; PASS=$((PASS + 1)); }
warn() { echo "WARN  $*"; WARN=$((WARN + 1)); }
fail() { echo "FAIL  $*"; FAIL=$((FAIL + 1)); }

ROOT_ENV="${REPO_ROOT}/.env"
BACKEND_ENV="${BACKEND_ENV_FILE:-${REPO_ROOT}/backend_ai/backend/.env}"
# Prefer dedicated backend env; else use root .env (typical docker compose single file).
EFFECTIVE_DB_ENV=""
if [[ -f "$BACKEND_ENV" ]]; then
  EFFECTIVE_DB_ENV="$BACKEND_ENV"
elif [[ -f "$ROOT_ENV" ]]; then
  EFFECTIVE_DB_ENV="$ROOT_ENV"
fi

has_nonempty_key() {
  local file="$1" key="$2"
  [[ -f "$file" ]] || return 1
  grep -E "^${key}=" "$file" >/dev/null 2>&1 || return 1
  local v
  v=$(grep -E "^${key}=" "$file" | head -1 | cut -d= -f2- | tr -d '\r')
  [[ -n "${v// }" ]]
}

echo "== Preflight Linux control-plane =="
echo "REPO_ROOT=$REPO_ROOT"
echo ""

# --- Python ---
if command -v python3 >/dev/null 2>&1; then
  pass "python3 present ($(python3 --version 2>&1 | tr -d '\n'))"
else
  fail "python3 not found"
fi

# --- Repo layout ---
[[ -f "${REPO_ROOT}/backend_ai/backend/requirements.txt" ]] && pass "backend requirements.txt exists" || fail "missing backend_ai/backend/requirements.txt"
[[ -f "${REPO_ROOT}/hubbot/requirements.txt" ]] && pass "hubbot requirements.txt exists" || fail "missing hubbot/requirements.txt"
[[ -f "${REPO_ROOT}/docker-compose.yml" ]] && pass "docker-compose.yml exists" || fail "missing docker-compose.yml"
[[ -f "${REPO_ROOT}/ecosystem.config.js" ]] && pass "ecosystem.config.js exists" || warn "ecosystem.config.js missing"
[[ -f "${REPO_ROOT}/nginx.conf" ]] && pass "nginx.conf exists" || warn "nginx.conf missing"
[[ -f "${REPO_ROOT}/frontend-v2/package.json" ]] && pass "frontend-v2/package.json exists" || fail "missing frontend-v2/package.json"

# --- Env files (existence only) ---
if [[ -f "$ROOT_ENV" ]]; then
  pass "root .env exists (compose)"
else
  warn "root .env missing (ok if only PM2 + backend .env)"
fi

if [[ -f "$BACKEND_ENV" ]]; then
  pass "backend env file exists: $BACKEND_ENV"
else
  warn "backend env missing: $BACKEND_ENV (set BACKEND_ENV_FILE if elsewhere)"
fi

# --- Required keys (names only; never print values) ---
check_keys_in_file() {
  local label="$1" file="$2"; shift 2
  [[ -f "$file" ]] || return 0
  local k
  for k in "$@"; do
    if has_nonempty_key "$file" "$k"; then
      pass "${label}: key set — $k"
    else
      fail "${label}: missing or empty — $k"
    fi
  done
}

if [[ -f "$ROOT_ENV" ]]; then
  check_keys_in_file "compose .env" "$ROOT_ENV" "LOCAL_REDIS_PASSWORD"
  for k in TELEGRAM_BOT_TOKEN BACKEND_HOST; do
    if has_nonempty_key "$ROOT_ENV" "$k"; then
      pass "compose .env: key set — $k"
    else
      warn "compose .env: missing or empty — $k"
    fi
  done
fi

if [[ -n "$EFFECTIVE_DB_ENV" ]]; then
  check_keys_in_file "db/redis env (${EFFECTIVE_DB_ENV##*/})" "$EFFECTIVE_DB_ENV" "POSTGRES_HOST" "POSTGRES_USER" "POSTGRES_DB" "POSTGRES_PASSWORD"
  if has_nonempty_key "$EFFECTIVE_DB_ENV" "REDIS_URL" || has_nonempty_key "$EFFECTIVE_DB_ENV" "REDIS_WRITE_URL"; then
    pass "env: REDIS_URL or REDIS_WRITE_URL set"
  else
    fail "env: neither REDIS_URL nor REDIS_WRITE_URL set (checked $EFFECTIVE_DB_ENV)"
  fi
  if has_nonempty_key "$EFFECTIVE_DB_ENV" "BACKEND_API_KEY"; then
    pass "env: BACKEND_API_KEY set"
  else
    warn "env: BACKEND_API_KEY missing (required for hubbot/runner auth in prod)"
  fi
else
  warn "no backend or root .env — skip POSTGRES/REDIS/API key checks"
fi

# --- Redis TCP (optional; no URL printed) ---
if [[ -n "$EFFECTIVE_DB_ENV" ]] && command -v redis-cli >/dev/null 2>&1; then
  ru=""
  if has_nonempty_key "$EFFECTIVE_DB_ENV" "REDIS_WRITE_URL"; then
    ru=$(grep -E '^REDIS_WRITE_URL=' "$EFFECTIVE_DB_ENV" | head -1 | cut -d= -f2- | tr -d '\r')
  elif has_nonempty_key "$EFFECTIVE_DB_ENV" "REDIS_URL"; then
    ru=$(grep -E '^REDIS_URL=' "$EFFECTIVE_DB_ENV" | head -1 | cut -d= -f2- | tr -d '\r')
  fi
  if [[ -n "$ru" ]]; then
    if redis-cli -u "$ru" ping 2>/dev/null | grep -q PONG; then
      pass "Redis PING (redis-cli)"
    else
      warn "Redis not reachable or auth failed (check REDIS_* in env; not printing URL)"
    fi
  fi
elif [[ -n "$EFFECTIVE_DB_ENV" ]]; then
  warn "redis-cli not installed — skip Redis PING"
fi

# --- Postgres (pg_isready; no password echoed) ---
if [[ -n "$EFFECTIVE_DB_ENV" ]] && command -v pg_isready >/dev/null 2>&1; then
  ph=$(grep -E '^POSTGRES_HOST=' "$EFFECTIVE_DB_ENV" | head -1 | cut -d= -f2- | tr -d '\r')
  pp=$(grep -E '^POSTGRES_PORT=' "$EFFECTIVE_DB_ENV" | head -1 | cut -d= -f2- | tr -d '\r')
  pu=$(grep -E '^POSTGRES_USER=' "$EFFECTIVE_DB_ENV" | head -1 | cut -d= -f2- | tr -d '\r')
  [[ -n "$pp" ]] || pp=5432
  if [[ -n "$ph" && -n "$pu" ]]; then
    if pg_isready -h "$ph" -p "$pp" -U "$pu" >/dev/null 2>&1; then
      pass "Postgres pg_isready"
    else
      warn "Postgres pg_isready failed (host/port/user from env; password not tested here)"
    fi
  fi
elif [[ -n "$EFFECTIVE_DB_ENV" ]]; then
  warn "pg_isready not installed — skip Postgres check"
fi

# --- Backend HTTP /ready (local) ---
port=8001
if [[ -n "$EFFECTIVE_DB_ENV" ]] && grep -qE '^BACKEND_PORT=' "$EFFECTIVE_DB_ENV"; then
  port=$(grep -E '^BACKEND_PORT=' "$EFFECTIVE_DB_ENV" | head -1 | cut -d= -f2- | tr -d '\r')
  [[ -n "$port" ]] || port=8001
fi
if command -v curl >/dev/null 2>&1; then
  if curl -fsS -m 3 "http://127.0.0.1:${port}/ready" >/dev/null 2>&1; then
    pass "HTTP /ready on 127.0.0.1:${port}"
  else
    warn "HTTP /ready not OK (service down or different port — not a hard fail for cold server)"
  fi
else
  warn "curl not installed — skip /ready"
fi

echo ""
echo "======== SUMMARY ========"
echo "PASS: $PASS  WARN: $WARN  FAIL: $FAIL"
if [[ "$FAIL" -gt 0 ]]; then
  echo "RESULT: FAIL"
  exit 1
fi
echo "RESULT: OK (review WARN)"
exit 0
