#!/bin/bash
# PM2 cluster: each instance gets a unique port (8001 + instance_id).
# Set instance_var in ecosystem so each process gets a distinct id; nginx upstream 8001..8008.
set -e
cd "$(dirname "$0")/.."
INSTANCE_ID=${INSTANCE_ID:-${NODE_APP_INSTANCE:-0}}
HOST=${API_HOST:-${BACKEND_HOST:-127.0.0.1}}
PORT=$((8001 + INSTANCE_ID))
export PORT
exec venv/bin/python3 -m uvicorn app.main:app --host "$HOST" --port "$PORT" --workers 1 --limit-concurrency 1000
