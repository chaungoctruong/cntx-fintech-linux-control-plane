# Spider AI — Linux control plane (CNTX MT5 SaaS)

**Repo này** là control-plane trên Linux cho nền tảng Spider AI: **không** chạy MT5 trực tiếp; lệnh trade đi xuống **Windows runner** qua Redis/HTTP theo hợp đồng command/event.

**Mục lục nhanh:** [Tổng quan](#1-tổng-quan-dự-án) · [Quy tắc an toàn production](#2-quy-tắc-an-toàn-production) · [Kiến trúc](#3-tóm-tắt-kiến-trúc) · [Thư mục](#4-bản-đồ-thư-mục) · [VPS mới](#5-fresh-vps--setup-an-toàn) · [Biến môi trường](#6-biến-môi-trường-theo-nhóm) · [Lệnh an toàn](#7-lệnh-thường-dùng-chỉ-read-only--trạng-thái) · [Checklist deploy](#8-checklist-deploy) · [Windows runner](#9-tích-hợp-windows-runner) · [Compose A→Z](#10-local-dev--docker-compose-a-z) · [Troubleshooting](#11-troubleshooting) · [Scale](#12-gợi-ý-scale) · [Rollback tài liệu](#13-rollback-tài-liệu--repo)

**Chỉ mục tài liệu đầy đủ:** [docs/DOCS_INDEX.md](docs/DOCS_INDEX.md)

## Release freeze / server scaling

Guardrail khi đóng băng cấu hình hoặc thêm server control-plane (không thay logic runtime):

- [docs/CONFIG_MANIFEST_TEMPLATE.md](docs/CONFIG_MANIFEST_TEMPLATE.md) — manifest từng máy (placeholder, không secret)
- [docs/SCALE_NEW_SERVER_RUNBOOK.md](docs/SCALE_NEW_SERVER_RUNBOOK.md) — clone tag, venv, env, preflight, health
- [docs/RELEASE_FREEZE_CHECKLIST.md](docs/RELEASE_FREEZE_CHECKLIST.md) — checklist trước tag / scale

Preflight read-only: `bash ops/preflight_linux_control_plane.sh` (tuỳ chọn `BACKEND_ENV_FILE=...`).

---

## 1. Tổng quan dự án

| Thành phần | Vai trò |
|------------|---------|
| **backend_ai/** | FastAPI: API `/api/v2/*`, ingest runner, TradingView broadcast, orchestration; Postgres = source of truth; Redis = queue/stream/cache; serve static Mini App (`frontend-v2/out`) |
| **hubbot/** | Telegram bot (long-poll mặc định), gọi backend HTTP + `X-Backend-Api-Key` |
| **frontend-v2/** | Next.js 14, `output: "export"` — build ra HTML/JS tĩnh, mount vào backend |
| **Redis** | Command queue, event transport, cache (theo module) |
| **PostgreSQL** | User, account, deployment, command state, subscriptions, … |
| **Nginx** (tuỳ deploy) | TLS, reverse proxy tới Uvicorn — xem `nginx.conf` / `config/` |
| **runner/** (trong repo) | Stub/reference + tài liệu hợp đồng; runner production trên Windows là repo/process riêng |
| **bot-trading/** | Registry package bot trên Linux (`gsalgovip`), không chứa secret |
| **ops/** | Script HA/monitoring compose dev helper |
| **docs/** | Runbook bổ sung (mesh, TradingView, index) |

**Health (backend):** `GET /health` (chi tiết), `GET /ready` (readiness), `GET /api/v2/system/healthz` (probe legacy).

**TradingView (nếu bật):** public broadcast — xem [docs/TRADINGVIEW_MT5_WEBHOOK_RUNBOOK.md](docs/TRADINGVIEW_MT5_WEBHOOK_RUNBOOK.md).

---

## 2. Quy tắc an toàn production

- **Không** sửa `.env`, `.env.dev`, `secrets/*`, hoặc dán secret/token/password vào tài liệu, issue, log công khai.
- **Không** `FLUSHALL`, truncate/drop DB, hay thao tác xoá dữ liệu ngoài cửa sổ bảo trì có kế hoạch rollback.
- **Không** restart/kill process production (PM2, systemd, container) khi chưa có maintenance window và owner dịch vụ.
- **Không** deploy frontend/backend mới lên prod khi chưa có checklist (env build-time `NEXT_PUBLIC_*`, `BACKEND_API_KEY`, tunnel/domain).
- Mọi thay đổi **runtime** (Python/JS, router, migration) phải qua review riêng — README này chỉ định hướng vận hành.

---

## 3. Tóm tắt kiến trúc

```text
User (Telegram) ──► Hubbot (long-poll) ──HTTP──► FastAPI backend
                        │                        │
 Mini App (HTTPS) ◄───┴────────────────────────┤ static export
                                                 │
                    Postgres ◄──ORM/repo/state──┤
                    Redis ◄──queue/stream──────┤
                                                 │
 Windows Runner fleet ◄──commands / events──────┘
        │
        ▼
     MT5 ──► Broker
```

- **Control plane** (Linux): quyết định, lưu state, publish command.
- **Execution plane** (Windows): MT5 terminal, worker/slot, báo event ngược.

---

## 4. Bản đồ thư mục

| Path | Mô tả ngắn |
|------|------------|
| `backend_ai/` | Dockerfile build `spider-app`; code FastAPI dưới `backend_ai/backend/` |
| `hubbot/` | Dockerfile `hubbot`; `requirements.txt` riêng |
| `frontend-v2/` | Next 14; `package.json` + `package-lock.json` — **Node 20** khi build trong Linux container |
| `bot-trading/` | Package bot chuẩn hoá; không có `requirements.txt` Python |
| `runner/` | Tham chiếu schema / prompt tích hợp Windows |
| `config/` | Ghi chú nginx / handoff cấu hình |
| `docs/` | Runbook + **[DOCS_INDEX.md](docs/DOCS_INDEX.md)** |
| `ops/` | Compose dev helper, monitoring notes |
| `logs/` | Thư mục log runtime (thường bind mount) — không commit |
| `secrets/` | Chỉ trên máy chủ — **không** commit |
| `nginx.conf` | Baseline sample |
| `docker-compose.yml` | Dev/local: `db`, `redis`, `spider-app`, `hubbot` |
| `ecosystem.config.js` | PM2 mẫu (path host hard-code — chỉnh theo server thật) |
| `DEPLOY_FRESH_VPS.md` | Checklist VPS Rocky/RHEL + Docker |
| `CLAUDE.md` | Bối cảnh kỹ thuật sâu (canonical cho AI/engineer) |

**Dependencies Python:** `backend_ai/backend/requirements.txt` (image mặc định), `hubbot/requirements.txt`; tùy chọn `requirements-ai-vector.txt`, `requirements-ai-lora.txt`; snapshot tham chiếu `requirements_enterprise_v1.txt`.

---

## 5. Fresh VPS & setup an toàn

1. Đọc [DEPLOY_FRESH_VPS.md](DEPLOY_FRESH_VPS.md) (checklist có **CHECK** và tách **Maintenance window only**).
2. **OS:** Rocky 9 / RHEL family như runbook; **Docker 24+** + compose v2 cho luồng compose.
3. **Python:** **3.11** khớp `FROM python:3.11-slim` trong `backend_ai/Dockerfile`. PM2/host install: tạo venv riêng backend và hubbot, `pip install -r` đúng file từng service (không gộp lung tung).
4. **Node:** **20 LTS** cho build Mini App (không build Next trên Windows native — lỗi ESM drive letter; dùng container Linux như runbook).
5. **Postgres / Redis:** Compose dùng `postgres:16-alpine`, `redis:7-alpine`; production nên managed/HA riêng.
6. **Env:** copy template thủ công từ README/CLAUDE (không commit secret); `chmod 600 .env`.
7. **Kiểm tra:** `curl` `/ready` localhost sau khi stack đã được phép chạy trong bảo trì.

---

## 6. Biến môi trường theo nhóm

*Chỉ tên biến và ý nghĩa — không ghi value thật.*

### Backend / API

| Biến | Bắt buộc | Mô tả |
|------|-----------|--------|
| `BACKEND_HOST` / `API_HOST` | Có (compose) | Trong container phải `0.0.0.0` để port-forward |
| `BACKEND_API_KEY` | Có (prod) | Hubbot ↔ backend ↔ runner; header `X-Backend-Api-Key` |
| `PUBLIC_BASE_URL` | Có cho Mini App | HTTPS; menu Telegram + URL public |
| `DRY_RUN` | Tuỳ | `1` = không đẩy lệnh thật xuống runner |

### Database

| Biến | Bắt buộc | Mô tả |
|------|-----------|--------|
| `LOCAL_POSTGRES_*` | Compose | User/db/password cho service `db` (xem `docker-compose.yml`) |

### Redis

| Biến | Bắt buộc | Mô tả |
|------|-----------|--------|
| `LOCAL_REDIS_PASSWORD` | Compose | Bắt buộc trong `docker-compose.yml` hiện tại |
| `REDIS_URL` | Có | URL kết nối, có thể gồm password |
| `BOT_COMMAND_QUEUE_REDIS_URL` | Tuỳ | Hàng đợi lệnh bot nếu tách URL |

### Telegram

| Biến | Bắt buộc | Mô tả |
|------|-----------|--------|
| `TELEGRAM_BOT_TOKEN` | Có | Một token ↔ một consumer long-poll |

### Runner / control plane

| Biến | Bắt buộc | Mô tả |
|------|-----------|--------|
| `RUNNER_CONTROL_PLANE_URL` | Khuyến nghị | URL công khai runner gọi register/heartbeat/API runner |
| `BACKEND_URL` | Có (hubbot) | Trong Docker Compose: **`http://spider-app:8001`** (nội bộ). Không nhất thiết trùng `PUBLIC_BASE_URL` |

### TradingView / Webhook

| Biến | Bắt buộc | Mô tả |
|------|-----------|--------|
| *(endpoint public)* | Tuỳ | `POST /api/v2/public/tradingview/broadcast` — xem runbook TradingView |

### Frontend (build-time)

| Biến | Bắt buộc | Mô tả |
|------|-----------|--------|
| `NEXT_PUBLIC_BACKEND_URL` | Có khi build | Inline vào bundle — đổi URL phải **rebuild** |
| `NEXT_PUBLIC_API_URL` | Thường cùng base | Giống trên |

Chi tiết logging, login lease, v.v.: [CLAUDE.md](CLAUDE.md).

---

## 7. Lệnh thường dùng (chỉ read-only / trạng thái)

```bash
git status
git rev-parse --show-toplevel
python3 --version
test -f .env && echo ".env exists" || echo ".env missing"
test -f backend_ai/backend/requirements.txt && echo backend requirements OK
test -f hubbot/requirements.txt && echo hubbot requirements OK
curl -fsS http://127.0.0.1:8001/ready 2>/dev/null | head -c 120 || echo "backend not listening on :8001"
pm2 status 2>/dev/null || true
systemctl is-active nginx 2>/dev/null || true
systemctl is-active redis 2>/dev/null || true
systemctl is-active postgresql 2>/dev/null || true
docker compose ps 2>/dev/null || true
```

*(Lệnh có tác động dịch vụ — restart, `down -v`, migration — chỉ trong cửa sổ bảo trì; xem [DEPLOY_FRESH_VPS.md](DEPLOY_FRESH_VPS.md) mục “Maintenance window” và mục 10 bên dưới.)*

---

## 8. Checklist deploy

**Preflight**

- [ ] Đúng branch/tag release; diff đã review.
- [ ] `BACKEND_API_KEY` đồng bộ backend / hubbot / runner.
- [ ] `NEXT_PUBLIC_*` đã rebuild nếu đổi domain/tunnel.

**Hạ tầng**

- [ ] Postgres reachable, connection pool đủ (theo tải).
- [ ] Redis reachable, password/TLS đúng môi trường.

**Ứng dụng**

- [ ] `GET /ready` = OK sau khi triển khai được phép.
- [ ] Hubbot: một instance / token; rõ long-poll vs webhook.
- [ ] Nginx route đúng host → upstream Uvicorn.
- [ ] Runner: transport (`redis_queue` vs HTTP poll) khớp ops target.

**Rollback plan**

- [ ] Giữ image/tag/git SHA trước khi deploy; rollback runtime = checkout + redeploy đã thống nhất — **không** xoá DB.

---

## 9. Tích hợp Windows runner

- Linux **dispatch** `RunnerCommand` (START/STOP/UPDATE, …) xuống queue/stream theo `runner_id` / account.
- Runner **callback** event (`BOT_STOPPED`, heartbeat, …) về API `/api/v2/runner/*` với cùng API key contract.
- **Một account active:** tránh hai deployment/bot “running” cùng lúc trừ khi kiến trúc product cho phép — xem [CLAUDE.md](CLAUDE.md) mục one-active-deployment.
- **Slot/sticky:** không gán nhầm account sang slot reserved của user khác; ưu tiên identifier ổn định (`account_id`, `deployment_id`) thay vì path/profile tạm thời.
- Handoff node cụ thể: [WINDOWS_RUNNER_HANDOFF_runner-win-01.md](WINDOWS_RUNNER_HANDOFF_runner-win-01.md) — **đối chiếu** với môi trường thật trước khi làm theo.

---

## 10. Local dev — Docker Compose (A→Z)

### Tài liệu theo vai trò

| Tài liệu | Khi nào đọc |
|----------|-------------|
| [README.md](README.md) | Entry chính (file này) |
| [docs/DOCS_INDEX.md](docs/DOCS_INDEX.md) | Tất cả link doc |
| [backend_ai/backend/README.md](backend_ai/backend/README.md) | Chi tiết backend |
| [frontend-v2/README.md](frontend-v2/README.md) | Mini App, env build |
| [bot-trading/README.md](bot-trading/README.md) | Registry bot |
| [runner/README.md](runner/README.md) | Hợp đồng runner |
| [config/README.md](config/README.md) | Nginx / sở hữu file cấu hình |

**Quy ước file env**

- Repo root `.env` (hoặc `ENV_FILE`) là runtime chính cho `docker compose` (đã `.gitignore`).
- `backend_ai/backend/.env` khi chạy backend ngoài compose (PM2/host).
- `frontend-v2/.env` khi dev frontend riêng.
- Runner Windows: `.env` riêng trên máy Windows.

### 10.1. Yêu cầu hệ thống

| Bắt buộc | Phiên bản tối thiểu |
|----------|---------------------|
| Docker + compose v2 | Docker 24+ |
| Node (chỉ khi build frontend) | **20 LTS** |

Tuỳ chọn: `cloudflared` / `ngrok` (HTTPS cho Mini App), `psql` client.

### 10.2. Chuẩn bị `.env` (compose)

`docker-compose.yml` đọc `${ENV_FILE:-.env}`. Thêm các biến bắt buộc theo comment trong file compose (ví dụ `LOCAL_REDIS_PASSWORD`).

Ví dụ khung (không copy secret thật):

```env
BACKEND_HOST=0.0.0.0
API_HOST=0.0.0.0
TELEGRAM_BOT_TOKEN=<from-botfather>
BACKEND_URL=http://spider-app:8001
PUBLIC_BASE_URL=https://<your-https-base>
RUNNER_CONTROL_PLANE_URL=https://<public-control-plane>
LOCAL_REDIS_PASSWORD=<strong-password>
```

### 10.3. Khởi chạy compose

```bash
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8001/health | jq .ok
curl -fsS http://127.0.0.1:8001/ready  | jq .ok
```

### 10.4. Mini App — tunnel HTTPS + build frontend

Telegram yêu cầu **HTTPS** cho `web_app`. Ví dụ Cloudflare quick tunnel (URL random):

```bash
cloudflared tunnel --url http://localhost:8001
```

Cập nhật `PUBLIC_BASE_URL` / `RUNNER_CONTROL_PLANE_URL` trong `.env`; **giữ** `BACKEND_URL=http://spider-app:8001`.

Build trong container Linux (đổi đường dẫn volume cho đúng máy bạn):

```bash
docker run --rm \
  -v "/path/to/linux-root-backend-hubot-v1/frontend-v2:/app" -w /app \
  -e NEXT_PUBLIC_BACKEND_URL=https://<your-tunnel> \
  -e NEXT_PUBLIC_API_URL=https://<your-tunnel> \
  node:20-bookworm-slim \
  bash -c "rm -rf node_modules out .next && npm install --no-audit --no-fund && npm run build"
```

`NEXT_PUBLIC_*` inline tại build — đổi URL phải build lại.

Sau đó (trong bảo trì) recreate/restart service để pick env + mount — xem [DEPLOY_FRESH_VPS.md](DEPLOY_FRESH_VPS.md).

### 10.5. Lệnh vận hành compose (có tác động)

```bash
docker compose logs -f spider-app
docker compose logs -f hubbot
docker compose restart hubbot
docker compose up -d --no-deps spider-app
docker compose exec spider-app bash
docker compose exec db psql -U spider_dev -d spider_dev
```

Migration thủ công (chỉ khi team vận hành chủ động chạy):

```bash
docker compose exec spider-app bash -lc 'cd /app/backend_ai/backend && alembic upgrade head'
docker compose exec spider-app bash -lc 'cd /app/backend_ai/backend && alembic current'
```

Quy ước: DB mới → `alembic upgrade head`; DB đã có schema từ `init_pg_schema` → có thể cần `alembic stamp head` — chi tiết [backend_ai/backend/migrations/README.md](backend_ai/backend/migrations/README.md).

### 10.6. Reset & cleanup (nguy hiểm)

```bash
docker compose down
docker compose down -v
```

`down -v` **xoá volume** Postgres + Redis.

---

## 11. Troubleshooting

| Hiện tượng | Hướng xử lý |
|------------|-------------|
| `/ready` fail | Đọc JSON `/health`; kiểm tra Postgres + Redis URL/password |
| Nginx sai vhost | So khớp `server_name` và upstream port (8001 / 8002+ theo PM2) |
| Redis connection fail | Password, bind `127.0.0.1` vs public, firewall |
| Postgres “too many connections” | Giảm pool app hoặc tăng `max_connections` / scale read |
| Telegram `Conflict getUpdates` | Hai process cùng token — dừng một bên |
| Runner offline | Heartbeat, tailnet/firewall, `RUNNER_CONTROL_PLANE_URL` |
| Command kẹt queued/pending | Redis, reconciler, log `runner.command.*` (xem CLAUDE runbook) |
| Callback timeout | Latency mạng, runner load, timeout HTTP |
| TradingView 4xx/5xx | Payload alert, auth, URL user webhook nếu có chuỗi giao hàng |
| Frontend sai API | Rebuild với `NEXT_PUBLIC_*` đúng |

Chi tiết lỗi đã biết: [CLAUDE.md](CLAUDE.md) phần Troubleshooting / Runbook.

---

## 12. Gợi ý scale

- **Backend Linux:** scale ngang instance API (PM2 / LB) — đảm bảo idempotent command + Postgres truth.
- **Runner Windows:** scale theo `runner_id`/máy; không nhồi quá nhiều MT5 trên một OS không đủ RAM/CPU.
- **Redis/DB:** pool, backpressure, reconciler; Redis chỉ transport.
- **Lane tách:** nếu sau này có broker API-only (cTrader, …) có thể tách lane runner — product decision.

---

## 13. Rollback tài liệu / repo

- Chỉ đổi doc/requirements comment trong release này → rollback bằng `git checkout -- <file>`.
- **Không** “rollback” production DB/Redis state bằng tay qua các lệnh trong README.

---

## 14. Tham khảo endpoint

| Endpoint | Mục đích |
|----------|----------|
| `GET /health` | Liveness chi tiết |
| `GET /ready` | Readiness cho LB |
| `GET /api/v2/system/healthz` | Probe legacy |
| `GET /` (public) | Mini App static |

---

## 15. Kiến trúc deploy production (PM2)

Theo [ecosystem.config.js](ecosystem.config.js): path mẫu trên Linux host (`/root/spider-ai/...`) — **chỉnh** cho đúng server. Compose vẫn là cách khuyến nghị cho dev trên Windows/macOS.
