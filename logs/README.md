# Runtime Logs

This folder is the central local log root. Real `*.log` and `*.jsonl` files are
ignored by Git.

- `backend/` - FastAPI control-plane logs and backend worker scripts.
- `hubbot/` - Telegram Hubbot service logs and Hubbot debug JSONL traces.
- `pm2/` - PM2 stdout/stderr capture for managed services.
- `runner/` - Reserved for runner-side logs when a Linux runner process is added.

Expected files include:

- `backend/api-instance-<n>.log` and `backend/api-instance-<n>.error.log`
- `backend/runner-event-consumer.log`
- `backend/mt5-runner-stub.log`
- `hubbot/hubbot.log` and `hubbot/hubbot.error.log`
- `hubbot/hubbot-debug-radar.jsonl` and `hubbot/hubbot-debug-lock.jsonl`
- `runner/gsalgovip.log` and `runner/gsalgovip.error.log`
- `pm2/spider-backend.out.log`, `pm2/spider-backend.error.log`
- `pm2/spider-hubbot.out.log`, `pm2/spider-hubbot.error.log`

Default rotation is controlled by `LOG_MAX_BYTES` and `LOG_BACKUP_COUNT`.
Override the root path with `CNTX_LOG_DIR` or `LOG_DIR`.
Hubbot can also use `HUBBOT_LOG_DIR` for its final service log folder.

Quick checks:

```bash
tail -f logs/backend/api-*.log
tail -f logs/backend/*.error.log
tail -f logs/hubbot/hubbot.log
tail -f logs/hubbot/*.error.log
tail -f logs/pm2/*.error.log
```
