#!/usr/bin/env bash
# Run docker compose against the dev env file (.env.dev).
#
# Usage:
#   ops/compose-dev.sh up -d
#   ops/compose-dev.sh logs -f spider-app
#   ops/compose-dev.sh restart spider-app
#
# Both `--env-file` (compose interpolation) and ENV_FILE (service env_file)
# point at the same file so LOCAL_* vars and container env stay in sync.
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_PATH=".env.dev"
if [[ ! -f "$ENV_PATH" ]]; then
  echo "[compose-dev] ERROR: $ENV_PATH not found in $(pwd)" >&2
  exit 1
fi

export ENV_FILE="$ENV_PATH"
exec docker compose --env-file "$ENV_PATH" "$@"
