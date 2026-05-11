# Configuration manifest — template (no secrets)

Điền một bản **mỗi server** (hoặc mỗi vai trò) khi release freeze / chuyển máy. Lưu ngoài git hoặc trong vault nội bộ (file manifest **không** chứa password/token — chỉ path và version).

| Field | Value (placeholder) |
|-------|---------------------|
| **Manifest ID** | `<ENV>-<ROLE>-<YYYYMMDD>` |
| **Server role** | `linux-control-plane` \| `linux-control-plane-secondary` \| `redis` \| `postgres` \| *(other)* |
| **Hostname** | `<FQDN-or-short-name>` |
| **Public IP / DNS** | `<public-ip-or-dns>` |
| **Private / tailnet IP** | `<100.x.x.x or RFC1918>` |
| **Repo path** | `/path/to/linux-root-backend-hubot-v1` |
| **Git remote (name)** | `origin` |
| **Branch / tag** | `<release/vX.Y.Z or commit SHA>` |
| **Python (runtime)** | `3.11.x` (khớp `backend_ai/Dockerfile` / venv host) |
| **Node (build Mini App)** | `20.x` (LTS, build trong container Linux) |
| **Env file — compose** | `<repo>/.env` hoặc `ENV_FILE` đang dùng |
| **Env file — backend PM2** | `<repo>/backend_ai/backend/.env` |
| **Env file — hubbot** | `<repo>/hubbot/.env` *(nếu tách)* |
| **Service manager** | `docker compose` \| `pm2` + `systemd` unit names |
| **Backend bind** | `BACKEND_HOST` / port (vd. `0.0.0.0:8001` compose, `8002+` PM2) |
| **Public base URL** | `https://<domain>` (Mini App / Telegram web_app) |
| **Runner control plane URL** | `https://<domain>` hoặc tailnet `http://100.x.x.x:8001` |
| **Redis endpoint (logical)** | `REDIS_URL` / `REDIS_WRITE_URL` — **ghi “set in .env”, không paste URL có password** |
| **Postgres endpoint (logical)** | `POSTGRES_HOST:PORT` + db name — **không paste password** |
| **Hubbot → backend** | `BACKEND_URL` (compose: `http://spider-app:8001`) |
| **Nginx vhost path** | `/etc/nginx/...` *(nếu dùng)* |
| **PM2 ecosystem path** | `<repo>/ecosystem.config.js` (đã chỉnh cwd/port chưa) |
| **Frontend static path** | `<repo>/frontend-v2/out` (build tag + `NEXT_PUBLIC_*` snapshot) |
| **runner_id / node_id** | N/A trên Linux CP; ghi runner fleet map ở doc Windows |
| **MT5 template / slot** | N/A trên Linux CP |
| **max safe slots** | N/A (Windows runner doc) |
| **Health check URL** | `http://127.0.0.1:<port>/ready` và public `https://<domain>/ready` |
| **Logs directory** | `<repo>/logs` hoặc path syslog/journald |
| **Backup config location** | Path tarball / vault ref **không** chứa secret inline |
| **Owner / on-call** | `<team contact>` |
| **Last verified** | `<date>` |
| **Rollback note** | Tag trước: `<git tag>`; image tag: `<container digest>`; “không rollback DB tay từ manifest” |

---

## Rollback (reference)

- **Git:** `git checkout <previous-tag>` rồi redeploy theo runbook — chỉ khi đã có maintenance window.
- **Config:** khôi phục tarball `.env` / nginx từ backup đã ghi ở trên — không dùng manifest để lưu secret.

---

## Sign-off

| Role | Name | Date |
|------|------|------|
| Release engineer | | |
| Ops | | |
