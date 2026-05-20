#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RELEASE="${MINIAPP_RELEASE:-$(date -u +%Y%m%d%H%M%S)}"
PUBLIC_URL="${PUBLIC_BASE_URL:-https://cntxlabs-miniapp.vercel.app}"
ENV_FILE="${ENV_FILE:-.env}"

echo "[miniapp] release=${RELEASE}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[miniapp] missing env file: $ENV_FILE" >&2
  exit 1
fi

if grep -q '^MINIAPP_RELEASE=' "$ENV_FILE"; then
  sed -i "s/^MINIAPP_RELEASE=.*/MINIAPP_RELEASE=${RELEASE}/" "$ENV_FILE"
else
  awk -v release="$RELEASE" '
    { print }
    /^PUBLIC_BASE_URL=/ && !done {
      print "MINIAPP_RELEASE=" release
      done=1
    }
    END {
      if (!done) {
        print "MINIAPP_RELEASE=" release
      }
    }
  ' "$ENV_FILE" > "${ENV_FILE}.tmp"
  mv "${ENV_FILE}.tmp" "$ENV_FILE"
fi

export NEXT_PUBLIC_RELEASE="$RELEASE"
npx --yes vercel deploy --prod --yes

docker compose up -d --force-recreate --no-deps hubbot

echo "[miniapp] hubbot URL:"
docker compose exec -T hubbot bash -lc 'cd /app/hubbot && python - <<'"'"'PY'"'"'
from app.keyboards import miniapp_home_url
print(miniapp_home_url())
PY'

echo "[miniapp] public /bot headers:"
curl -fsSI "${PUBLIC_URL%/}/bot/?v=${RELEASE}" | sed -n '1,16p'
