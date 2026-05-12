#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

PUBLIC_URL="${PUBLIC_BASE_URL:-}"
if [[ -z "$PUBLIC_URL" && -f .env ]]; then
  PUBLIC_URL="$(grep -E '^PUBLIC_BASE_URL=' .env | tail -n1 | cut -d= -f2-)"
fi
PUBLIC_URL="${PUBLIC_URL%/}"
if [[ -z "$PUBLIC_URL" ]]; then
  echo "ERROR: Set PUBLIC_BASE_URL (env) or add PUBLIC_BASE_URL=... to repo root .env before running this script." >&2
  echo "       Refusing a baked-in default URL (product: no surprise traffic to the wrong host)." >&2
  exit 1
fi

echo "[1/4] Docker services"
docker compose ps

echo
echo "[2/4] Backend local /ready"
curl -fsS http://127.0.0.1:8001/ready >/dev/null
echo "OK"

echo
echo "[3/4] Public health"
curl -fsS "$PUBLIC_URL/api/v2/system/healthz" >/dev/null
echo "OK"

echo
echo "[4/4] Public bot catalog"
python3 - "$PUBLIC_URL" <<'PY'
from __future__ import annotations
import json
import sys
import urllib.request

base = sys.argv[1].rstrip("/")
with urllib.request.urlopen(f"{base}/api/v2/bots", timeout=15) as resp:
    payload = json.loads(resp.read().decode("utf-8"))
items = payload.get("items") or []
codes = [item.get("bot_code") or item.get("bot_id") for item in items]
if not codes:
    raise SystemExit("bot catalog is empty")
print("OK bots=" + ",".join(codes))
PY
