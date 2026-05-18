#!/usr/bin/env bash
# Run docker compose against the prod env file (.env).
#
# Usage:
#   ops/compose-prod.sh up -d
#   ops/compose-prod.sh logs -f cntx-lab
#   ops/compose-prod.sh restart cntx-lab
#
# Refuses to run if .env is missing — you must populate prod values first.
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_PATH=".env"
if [[ ! -f "$ENV_PATH" ]]; then
  echo "[compose-prod] ERROR: $ENV_PATH not found. Populate it with prod values before running." >&2
  exit 1
fi

# docker compose already auto-reads .env; pass --env-file explicitly so the
# behaviour matches compose-dev.sh and any future overrides stay obvious.
export ENV_FILE="$ENV_PATH"
exec docker compose --env-file "$ENV_PATH" "$@"
