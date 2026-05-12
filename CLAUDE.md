# CLAUDE.md — Spider AI (Linux backend + Telegram bot + Mini App)

Tài liệu này dành cho Claude Code khi làm việc trong repo `linux-root-backend-hubot-v1`. Mục tiêu: nắm đúng kiến trúc monorepo, các đường ranh giới giữa backend / hubbot / frontend / runner, và những chỗ dễ dẫm chân khi vận hành.

---

## 1. Project là gì

**Spider AI** — control-plane của một nền tảng trade MT5 tự động qua Telegram. Repo này chứa **3 service** + **1 stub runner reference**, và là phía Linux của hệ kiến trúc 2-plane:

- **Linux (repo này)** = control-plane: quản lý user, account, deployment, lệnh, state, AI assistant. Không bao giờ trade trực tiếp.
- **Windows (repo `windowns-runner-mt5-user-v1`)** = execution-plane: chạy MT5, gửi lệnh broker, báo state ngược về.
- Postgres = source of truth. Redis = transport (command queue + event stream + cache).

Sơ đồ:

```
User (Telegram) ──▶ Mini App (Next.js) ──HTTPS──▶ FastAPI control-plane
                                                     │
                                  ┌──────────────────┼──────────────────┐
                                  ▼                  ▼                  ▼
                              Postgres            Redis             Hubbot
                                                     │                  │
                                                     │           long-poll Telegram
                                                     ▼
                                            Windows Runner Fleet
                                                     │
                                                     ▼
                                              MT5 → Broker
```

---

## 2. Layout monorepo

```
backend_ai/                      # FastAPI control-plane
  Dockerfile
  backend/
    app/
      main.py                    # FastAPI entry — đọc đầu tiên để hiểu wiring
      settings.py                # Pydantic settings (đọc env + defaults)
      api/v2/                    # HTTP routers: accounts, bots, deployments, runners, miniapp, streams, admin, wallet, …
      orchestration/             # deployment_manager, account_verification_manager, deployment_config, scheduler, start_failure_policy, runner_payload_identity
      events/                    # runner_event_ingest, runner_event_consumer, command_router, command_delivery_reconciler, webhook_delivery_service
      runner/                    # control_plane_client, queue_consumer, protocol — phía Linux nói chuyện với Windows runner
      services/                  # control_plane_service, store_service, watchdog, bot_catalog_service, miniapp_*, runner_gsalgo_state, broker/
      repositories/              # control_plane_repository (+ control_plane/ subfolder)
      schemas/                   # control_plane.py (+ ctrader.py legacy)
      models/                    # SQLAlchemy + Pydantic domain models
      ai/                        # AI assistant: routes_ai, care_campaign_service, continuous_learning, deferred_queue (+ knowledge ingestion)
      core/                      # rate_limit, redis_client, log_filters, log_hygiene
      monitoring/                # control_plane_metrics, control_plane_reconciler
      risk/                      # circuit_breaker_scheduler
      providers/, infra/, bot_catalog/, security.py, store.py
    migrations/                  # Alembic — đọc migrations/README.md trước khi tạo revision
    init_pg_schema.py            # Init schema idempotent ở startup (KHÔNG thay thế Alembic)
    scripts/                     # run_api.py (PM2 entry), run_runner_event_consumer.py, ingest knowledge, evaluate AI training, …
    static/                      # Asset backend serve trực tiếp
    requirements*.txt            # Pin Python deps (split: base / ai-vector / ai-lora / enterprise)
    alembic.ini

hubbot/                          # Telegram bot (python-telegram-bot, long-poll)
  Dockerfile
  main.py                        # Entry: build Application, register handlers, run_polling
  app/
    commands/                    # /start, /ping, /trangthai, /sys (dev status)
    callback/                    # CallbackQuery router
    consumer/                    # rabbitmq_commands (consume từ queue → reply Telegram)
    api/                         # client gọi backend FastAPI + ai_chat
    lifecycle/                   # single_instance lock, shutdown, alerts, error_handlers, runtime hooks
    config.py, message.py, keyboards.py, state.py, formatters.py, debug.py

frontend-v2/                     # Next.js 14 Mini App (static export)
  app/, components/, hooks/, lib/, public/, scripts/, styles/
  next.config.mjs                # `output: "export"` → ra `out/`
  package.json, tsconfig*.json, tailwind.config.ts
  out/                           # Build artifact — backend mount /app/frontend-v2/out:ro

runner/                          # Stub reference, KHÔNG phải Windows runner thật.
                                 # Windows runner production nằm ở repo `windowns-runner-mt5-user-v1`.

config/                          # nginx-spider.conf (+ README)
docker-compose.yml               # Local/dev: db + redis + spider-app + hubbot
ecosystem.config.js              # PM2: spider-backend + spider-hubbot (`PROJECT_ROOT`)
vercel.json                      # Tuỳ chọn: Vercel build Mini App + rewrites → backend Linux
nginx.conf                       # Nginx baseline
ops_telegram_alerts.py           # Telegram error notifier shared (top-level vì backend import top-level)
DEPLOY_FRESH_VPS.md              # SOP deploy fresh VPS (Rocky 9)
README.md                        # Hướng dẫn chạy A→Z (luồng compose)
.env.linux.example               # Default env compose (committed)
.env.linux                       # Override per-machine (gitignored)
```

---

## 3. Service contract (đọc trước khi đụng API)

### 3.1 backend (`spider-app`)

- **Framework**: FastAPI + Uvicorn. Bind `0.0.0.0:8001` trong container compose. Production PM2 dùng `API_PORT_BASE=8002` + `INSTANCE_ID` → 2 instance ở 8002 và 8003.
- **DB**: Postgres (16-alpine local). Schema khởi tạo qua `init_pg_schema.init_postgres_schema()` ở startup (idempotent). Migration mới đi qua Alembic — xem [backend_ai/backend/migrations/README.md](backend_ai/backend/migrations/README.md).
- **Cache/queue**: Redis (7-alpine local). Db index 0 = prod; dev có thể đổi `/1`.
- **Routers chính** ([app/api/v2/](backend_ai/backend/app/api/v2/)):
  - `accounts.py` — connect/verify MT5 account
  - `bots.py` — bot catalog (đọc-only từ user perspective, admin upsert qua admin route)
  - `deployments.py` — start/stop/config/commands/events/logs/performance
  - `runners.py` — endpoint nội bộ Windows runner (register, heartbeat, events, command delivery, packages, verification)
  - `miniapp.py` + `mini_router` — serve Mini App + API riêng
  - `streams.py` — SSE/long-poll cho client
  - `system.py` — `/health`, `/ready`, `/api/v2/system/healthz`
  - `me.py`, `wallet.py`, `rewards.py`, `public.py`, `public_status.py`, `admin.py`, `error_catalog.py`
- **Endpoint health**:
  - `GET /health` — liveness chi tiết (DB, Redis, runtime, AI)
  - `GET /ready` — readiness gọn cho LB
  - `GET /api/v2/system/healthz` — legacy nginx probe

### 3.2 hubbot (`spider-hubbot`)

- **Framework**: python-telegram-bot, long-poll qua `Application.run_polling()`. KHÔNG dùng webhook trong compose default.
- **Single instance**: `single_instance.py` lock — Telegram chỉ cho 1 consumer/`getUpdates` cùng token. Chạy 2 instance cùng token = `Conflict: terminated by other getUpdates request`.
- **Gọi backend qua `app/api/client.py`**, header `X-Backend-Api-Key` phải khớp `BACKEND_API_KEY` 2 phía.
- **Mini App menu button**: được set trong startup runtime hook nếu `PUBLIC_BASE_URL` là HTTPS hợp lệ. Telegram chặn HTTP — log warning `Menu button web app url '...' is invalid: only https links are allowed` nếu URL còn HTTP.

### 3.3 frontend-v2

- **Build**: Next.js 14, `output: "export"` → static HTML trong `frontend-v2/out/`.
- **Build BẮT BUỘC trong container Linux**, không chạy `next build` trên Windows native — bug Node ESM `ERR_UNSUPPORTED_ESM_URL_SCHEME ... Received protocol 'd:'`.
- **`NEXT_PUBLIC_*` được inline tại build time** — đổi `BACKEND_URL`/tunnel = phải build lại frontend.
- Backend mount `./frontend-v2/out:/app/frontend-v2/out:ro` qua compose, serve `/_next` và HTML qua catch-all.

### 3.4 runner/ trong repo này

- **Không phải Windows runner production**. Là stub/reference + `WINDOWS_RUNNER_INTEGRATION_PROMPT.md` (tiếng Việt) ở [backend_ai/backend/app/runner/](backend_ai/backend/app/runner/) làm hợp đồng để repo Windows implement đúng.
- Windows runner thật: repo `windowns-runner-mt5-user-v1`.

---

## 4. Hai chế độ deploy

### 4.1 Docker Compose (dev/local)

[docker-compose.yml](docker-compose.yml). 4 service: `db`, `redis`, `spider-app`, `hubbot`. Quy trình A→Z xem [README.md](README.md). Quy trình fresh VPS xem [DEPLOY_FRESH_VPS.md](DEPLOY_FRESH_VPS.md).

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f spider-app
docker compose logs -f hubbot
```

### 4.2 PM2 (production-style trên Linux host)

[ecosystem.config.js](ecosystem.config.js):
- **`spider-backend`**: `cwd` = `path.join(PROJECT_ROOT, "backend_ai/backend")` (mặc định `PROJECT_ROOT` là thư mục chứa `ecosystem.config.js`), venv `venv/bin/python3`, script `scripts/run_api.py`. 2 instance fork, port = `API_PORT_BASE` (8002) + `INSTANCE_ID` → **8002 và 8003**.
- **`spider-hubbot`**: `cwd` = `path.join(PROJECT_ROOT, "hubbot")`, venv `venv_hub/bin/python3`, script `main.py`. 1 instance.

Trên VPS đặt `PROJECT_ROOT` (env) nếu layout khác mặc định. Path tuyệt đối kiểu `/root/...` trong tài liệu cũ chỉ là ví dụ — luôn đối chiếu file `ecosystem.config.js` thực tế.

---

## 5. Env & secret

### File env

| File | Vai trò | Commit |
|---|---|---|
| `.env.linux.example` | Defaults compose local — luôn được load | ✅ |
| `.env.linux` | Override per-machine, chứa secret | ❌ (gitignored qua `.env*`) |
| `backend_ai/backend/.env.connect.example` | Adapter cTrader legacy, đã đóng băng | ✅ |
| `backend_ai/backend/.env.control-plane.example` | Baseline production-style | ✅ |
| `backend_ai/backend/.env.mt5-runner.example` | Cho runner Windows | ✅ |
| `backend_ai/backend/.env.redis.example` | Mẫu Redis prod | ✅ |
| `backend_ai/backend/.env` | Production thực | ❌ |
| `frontend-v2/.env.example` | Mẫu `NEXT_PUBLIC_*` (commit được) — `cp .env.example .env` rồi build |

### Biến quan trọng

| Biến | Ý nghĩa |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token bot — bắt buộc để hubbot khởi động. **Không dùng token prod cho dev.** |
| `BACKEND_HOST` / `API_HOST` | PHẢI là `0.0.0.0` trong container để Docker port-forward 8001 đến được |
| `PUBLIC_BASE_URL` | URL công khai HTTPS — Telegram bắt HTTPS cho `web_app`. Mini App URL build từ đây |
| `BACKEND_URL` | URL hubbot dùng để gọi backend |
| `BACKEND_API_KEY` | Khoá hubbot ↔ backend ↔ Windows runner. Phải khớp 3 phía. Header: `X-Backend-Api-Key` |
| `REDIS_URL`, `BOT_COMMAND_QUEUE_REDIS_URL` | DB index 0 = prod, dev có thể `/1` |
| `DRY_RUN` | `1` = không gửi lệnh thật xuống MT5 |
| `LOCAL_POSTGRES_*` | Override credential Postgres trong compose |
| `NEXT_PUBLIC_BACKEND_URL`, `NEXT_PUBLIC_API_URL` | **Build-time** cho frontend, đổi URL = rebuild |
| `LOG_LEVEL` | `INFO`/`DEBUG`/`WARNING`. Áp dụng cả backend lẫn hubbot |
| `STRUCTURED_LOG_FILE_ENABLED` | `1` (default) → ghi JSONL song song với `.log` text. Đặt `0` để tắt sink JSON |
| `REQUEST_LOG_ENABLED` | `1` (default) → backend log mỗi HTTP request kèm `request_id`/`status`/`elapsed_ms` |
| `SLOW_REQUEST_MS_THRESHOLD` | `1500` (default ms). Request 2xx/3xx vượt threshold → log `event=request.slow` (WARN). |
| `HUBBOT_HANDLER_LOG_ENABLED` | `1` (default) → hubbot log mỗi Telegram update với `user_id`/`chat_id`/`handler` |
| `CLIENT_TELEMETRY_ENABLED` | `1` (default) → endpoint `/api/v2/system/client-events` nhận log lỗi từ Mini App |
| `CLIENT_EVENT_LOG_PATH` | Override file ghi client telemetry (default `logs/frontend/client-events.jsonl`) |
| `DEBUG_TRACE_FILE_ENABLED` | `0` mặc định, `1` để bật debug trace bonus. **Production guard reject `=1`** |
| `LOGIN_LEASE_ENABLED` | `0` mặc định. `1` → bật tracking distributed lease cho MT5 login (Redis), không block dispatch (telemetry only) |
| `LOGIN_LEASE_ENFORCED` | `0` mặc định. `1` → conflict với owner khác → 409 `login_busy`; Redis down → 503 `login_lease_unavailable` (fail-closed). Chỉ honored khi `LOGIN_LEASE_ENABLED=1` |
| `LOGIN_LEASE_TTL_SEC` | `60` mặc định. TTL key `mt5:login_lease:{login}`. Renew mỗi heartbeat |

### Quy tắc

- Không commit `.env`, `.env.linux`, runtime secret. `.env*` đã gitignored.
- Hubbot/backend/Windows runner phải chia sẻ cùng `BACKEND_API_KEY`. Sai = 401/403 khắp nơi.
- Đổi tunnel URL → cập nhật `PUBLIC_BASE_URL` + `BACKEND_URL` + rebuild frontend + restart spider-app + hubbot.

---

## 6. Hợp đồng Linux ↔ Windows runner

### Auth
- Header `X-Backend-Api-Key: ${BACKEND_API_KEY}` cho mọi call vào `/api/v2/runner/*`.
- Windows runner KHÔNG được gọi API user/Mini App.

### Transport — production: Redis qua Headscale mesh
**Production target** (vài trăm runner + vài nghìn user + TradingView fan-out):
- **`redis_queue`**: backend pipeline LPUSH `mt5:runner:{RUNNER_ID}:commands` → runner BRPOP/BRPOPLPUSH. Latency ~50ms cho fan-out N user. Setup: [docs/HEADSCALE_MESH_SETUP.md](docs/HEADSCALE_MESH_SETUP.md).
- **HTTP** (register/heartbeat/events/bootstrap/delivery — request ngắn): runner gọi backend qua tailnet `http://100.64.0.1:8001`. Tránh proxy timeout ngắn (vd. Vercel) cho tải không phù hợp. **Điều khiển bot / lệnh thực thi** chỉ qua Redis `mt5:runner:{RUNNER_ID}:commands`.

**Bootstrap**: `GET /api/v2/runner/bootstrap?runner_id=runner-win-01` trả về contract đầy đủ — runner gọi sau khi cài Tailscale + join tailnet.

### Distributed network — Headscale mesh
- Headscale = open-source self-host server tương thích Tailscale client. Free unlimited (Tailscale official giới hạn 100 device). Chạy Docker trên Linux VPS.
- Mọi VPS (Linux backend + tất cả Windows runner) join cùng tailnet → IP private `100.64.0.0/10`.
- Redis bind chỉ trên tailnet IP → KHÔNG public.
- ACL: runner chỉ gọi backend được, không gọi nhau.
- Onboard runner mới: cài Tailscale client + 1 lệnh `tailscale up --login-server=...` với pre-auth key.

### TradingView fan-out
- Endpoint: `POST /api/v2/public/tradingview/broadcast` ([api/v2/tradingview_webhook.py](backend_ai/backend/app/api/v2/tradingview_webhook.py))
- Body: `{alert_id, signal_id, action: BUY|SELL|CLOSE, symbol, default_volume?}`
- Backend SELECT từ `tradingview_signal_subscriptions` table (account_id ↔ signal_id mapping) → build N command items → `CommandRouterService.dispatch_batch` → `RedisStreamPublisher.publish_command_batch` (1 pipeline) → tất cả runner pop song song.
- Idempotent: TradingView retry cùng `alert_id` → trace_id dedupe per (account_id, signal, action) → no double dispatch.
- Cap: `max_subscribers` mặc định 5000/broadcast.

### Hợp đồng dữ liệu
- `RunnerCommand` / `RunnerEvent` trong [backend_ai/backend/app/schemas/control_plane.py](backend_ai/backend/app/schemas/control_plane.py) PHẢI khớp với `runner/schemas/{commands,events}.py` ở repo Windows.
- Đổi schema = đồng bộ 2 repo cùng release. Wire format không backward-compatible nếu rename field — phối hợp với chủ repo Windows.

### Reconciler
- `CommandDeliveryReconcilerService` ([app/events/command_delivery_reconciler.py](backend_ai/backend/app/events/command_delivery_reconciler.py)) replay/requeue `START_BOT` / `STOP_BOT` còn `pending`/`queued` hoặc kẹt trong `processing` quá lâu.
- Redis publisher có dedupe marker theo `command_id`.
- **Postgres = source of truth**. Redis chỉ là transport. Mất Redis không mất state.

### Quy tắc one-active-deployment-per-account (go-live blocker đã ghi)
- Trước khi gửi `START_BOT` cho deployment mới của cùng account, backend PHẢI:
  1. Tìm deployment cũ đang chạy cùng `account_id`.
  2. Gửi `STOP_BOT` cho deployment cũ.
  3. Chờ `BOT_STOPPED` rõ ràng.
  4. Mới gửi `START_BOT` cho deployment mới.
- Match phải dùng cả `(account_id, deployment_id, runner_id, slot_id)`, không chỉ `account_id`. Vi phạm = orphan worker (đã có incident, xem repo Windows `docs/PROJECT_CONTEXT_FOR_CHATGPT_PRO.md`).

### DCA toggle (và config hot reload tương tự)
- Sử dụng `UPDATE_BOT_CONFIG`, KHÔNG tạo deployment mới, KHÔNG STOP/START worker. Fallback STOP/START chỉ khi runner trả unsupported/timeout.

### Distributed login lease (chống cùng MT5 login chạy 2 runner)
- **Risk**: cùng 1 MT5 login đăng nhập từ 2 IP đồng thời → broker reject/disconnect, position split, ban IP.
- **Implementation**: [backend_ai/backend/app/services/login_lease.py](backend_ai/backend/app/services/login_lease.py). Redis key `mt5:login_lease:{login}` lưu owner runner_id + command_id, TTL 60s, renew mỗi heartbeat.
- **Wire-up**:
  - **Acquire**: `command_router.dispatch` cho `START_BOT` (ngay sau khi insert command row, trước `publish_command`).
  - **Renew**: `runner_event_ingest.ingest_heartbeat` (qua reverse-index `mt5:login_lease:account:{account_id}` → login). Chi phí 1 GET + 1 EXPIRE/heartbeat.
  - **Release**: `runner_event_ingest.ingest_event` cho `BOT_STOPPED` / `SIGNAL_EXECUTOR_STOPPED`.
- **Rollout 2 phase**:
  - `LOGIN_LEASE_ENABLED=0` (default) → tất cả op no-op, KHÔNG ảnh hưởng dispatch.
  - `LOGIN_LEASE_ENABLED=1` + `LOGIN_LEASE_ENFORCED=0` → tracking + log conflict ở WARN (event=`login_lease.conflict`), KHÔNG block. Chạy canary để quan sát mức độ conflict thực tế.
  - `LOGIN_LEASE_ENABLED=1` + `LOGIN_LEASE_ENFORCED=1` → block: conflict → 409 `login_busy` (kèm `owner_runner_id`/`owner_command_id` trong `error_info`); Redis down → 503 `login_lease_unavailable` (fail-closed).
- **Idempotent**: same runner re-acquire chỉ refresh TTL, không reject.
- **Telegram alert** không tự động cho conflict (vì có thể do user manual switch). Theo dõi qua grep `event=login_lease.conflict|login_lease.renew.wrong_owner` trong `logs/backend/api.jsonl`.

---

## 7. Migration & schema

- **Khi VPS mới** (DB rỗng): `alembic upgrade head`.
- **Khi DB đang chạy với schema từ `init_pg_schema.py`**: `alembic stamp head` (đánh dấu, không thực thi).
- **Tạo revision mới**: chạy trong container.
  ```bash
  docker compose exec spider-app bash -lc 'cd /app/backend_ai/backend && alembic revision -m "msg"'
  docker compose exec spider-app bash -lc 'cd /app/backend_ai/backend && alembic upgrade head'
  ```
- `init_pg_schema.py` phải idempotent. Bug đã sửa: `_create_control_plane_scale_indexes(cur)` PHẢI gọi sau khi `runtime_logs` đã được tạo (không gọi giữa hàm). Khi sửa file này, đối chiếu fix trong commit `2cf8257`.

---

## 7.5 Logging architecture

Mọi service log qua stdlib `logging` + `RotatingFileHandler`. Khi enable đầy đủ có 3 sink song song:

1. **Console** (PM2/docker stdout) — text format, dễ đọc trong `docker logs` / `pm2 logs`.
2. **`logs/<service>/<base>.log`** + **`<base>.error.log`** — text format, kế thừa cũ.
3. **`logs/<service>/<base>.jsonl`** — structured JSON, mỗi dòng 1 record. Bật/tắt qua `STRUCTURED_LOG_FILE_ENABLED` (default `1`).

JSON record schema:
```json
{"ts": 1778..., "iso": "2026-...", "level": "INFO", "logger": "api.request",
 "msg": "request.end ...", "service": "api", "pid": 123, "host": "...",
 "request_id": "abc", "user_id": "42", "account_id": 7, "deployment_id": 99,
 "runner_id": "rx", "trace_id": "...", "http_status": 200, "elapsed_ms": 12.3}
```

Context fields tự inject từ `contextvars` ([backend_ai/backend/app/core/log_context.py](backend_ai/backend/app/core/log_context.py) và [hubbot/app/log_context.py](hubbot/app/log_context.py)). Mọi log line trong cùng request đều mang `request_id` mà không cần truyền tay.

**Backend** ([backend_ai/backend/app/core/request_logging.py](backend_ai/backend/app/core/request_logging.py)) gắn 1 ASGI middleware làm OUTERMOST, mỗi request:
- Đọc/sinh `X-Request-ID` (echo lại trên response).
- Bind context, log `request.end method=… path=… status=… elapsed_ms=…` (level theo status).
- Skip noise: `/health`, `/ready`, `/api/v2/system/healthz`, `/_next/`, `/static/`, `/favicon.ico`.

`runner_event_ingest` đã bind `account_id`/`deployment_id`/`runner_id`/`trace_id` ở mỗi `ingest_event` — mọi log con bên dưới đều thừa kế.

**Hubbot** ([hubbot/app/lifecycle/handler_logger.py](hubbot/app/lifecycle/handler_logger.py)) gắn `TypeHandler(Update)` ở group `-100` (pre) + group `9999` (post), mỗi update log `telegram.update.received`/`telegram.update.processed` với `user_id`/`chat_id`/`update_id`/`handler`/`elapsed_ms`. Legacy `_dbg` / `_dbg_lock` (debug-radar/lock JSONL) vẫn còn nguyên + đồng thời mirror sang stdlib logger để ra `hubbot.jsonl`.

**Frontend** ([frontend-v2/lib/clientLogger.ts](frontend-v2/lib/clientLogger.ts)) hook `window.onerror` + `unhandledrejection` (+ `pagehide` flush qua `navigator.sendBeacon`), batch và POST về `/api/v2/system/client-events`. Backend lưu `logs/frontend/client-events.jsonl` đồng thời mirror sang `api.client_event` logger nên cả 2 sink đều thấy.

**Grep nhanh**:
```bash
# Tất cả request lỗi 5xx trong 200 dòng cuối
tail -200 logs/backend/api-instance-0.jsonl | jq -c 'select(.http_status>=500)'

# Mọi log liên quan đến deployment 1234
grep -h '"deployment_id": 1234' logs/backend/*.jsonl | jq -c '{ts:.iso, level, msg, runner_id}'

# Update Telegram của user nào đó
grep -h '"user_id": "78901"' logs/hubbot/*.jsonl | jq -c '{ts:.iso, msg, handler, telegram_preview}'

# Lỗi client-side trong ngày
jq -c 'select(.severity=="error")' logs/frontend/client-events.jsonl | tail -50
```

---

## 7.6 Runbook — đọc log để fix nhanh

Mỗi failure mode dưới đây có 1 `event` ổn định trong JSONL. Grep `event` trước, đọc `hint` để biết hành động kế tiếp, rồi mới đọc `error_message`/`exc` nếu cần.

### Lệnh không xuống được Windows runner
```bash
# Mọi sự cố ở dispatch path
jq -c 'select(.event|test("runner\\.command\\."))' logs/backend/api.jsonl | tail -20
```
- `runner.command.dispatch.publish_failed` → Postgres đã ghi command nhưng Redis publish fail. Check Redis health, stream `mt5:account:{account_id}:commands`. Reconciler sẽ retry.
- `runner.command.replay_failed` → Reconciler retry vẫn fail. Check Redis + payload.
- `runner.command.requeue_failed` → Lệnh kẹt ở processing list của runner. Check `mt5:runner:{runner_id}:commands:processing` vs `mt5:runner:{runner_id}:commands` + Windows runner còn alive không.
- `runner.command.stale_start_reconcile_failed` → START_BOT timeout, reconciler không fail-fast được. Deployment có thể kẹt `start_requested`.
- `runner.command.dispatch.queued` (INFO) → Bình thường, lệnh đã vào Redis.

### Webhook user fail
```bash
jq -c 'select(.event|test("webhook\\."))' logs/backend/api.jsonl | tail -20
```
- `webhook.delivery.http_error` → URL của user trả 4xx/5xx. Sau 5 lần fail liên tiếp tự động deactivate webhook.
- `webhook.delivery.exception` → DNS/TLS/timeout. URL không reachable.
- `webhook.stream.process_failed` → Stream entry trong Redis bị malformed; entry không ack nên retry.

### Hubbot không gọi được backend
```bash
jq -c 'select(.event|test("hubbot\\.backend\\."))' logs/hubbot/hubbot.jsonl | tail -20
```
- `hubbot.backend.network_error` → Backend không reachable. Check `BACKEND_URL`, port 8001, `docker compose ps`.
- `hubbot.backend.5xx` → Backend trả 5xx. Cross-reference `request_id` sang `logs/backend/api.jsonl` để xem stack thật.
- `hubbot.backend.bad_json` → Reverse proxy có thể trả HTML error page. Check nginx + body_preview.
- `hubbot.backend.4xx` → Hubbot gửi sai body hoặc backend tighten validation.

### Backend API có exception 500
```bash
jq -c 'select(.event=="request.unhandled_exception")' logs/backend/api.jsonl | tail -10
# Lấy request_id từ output trên rồi:
grep -h '"request_id": "<id>"' logs/backend/api.jsonl | jq -c '{ts:.iso, event, msg, hint}'
```
Mọi exception 500 đều có `request_id` echo về client. User báo lỗi → xin `X-Request-ID` từ DevTools network tab → grep ra full stack.

### Request chậm
```bash
jq -c 'select(.event=="request.slow")' logs/backend/api.jsonl | tail -20
# Hoặc đặt threshold thấp hơn (ms):
SLOW_REQUEST_MS_THRESHOLD=500 docker compose up -d --no-deps spider-app
```

### Frontend / Mini App lỗi JS
```bash
jq -c 'select(.severity=="error")' logs/frontend/client-events.jsonl | tail -20
# Cross-reference cùng request_id với backend:
jq -r '.request_id' logs/frontend/client-events.jsonl | sort -u | tail -5
```

### Lỗi cụ thể chưa biết grep gì
```bash
# Mọi line ERROR trong 1h qua, gom theo event
jq -c 'select(.level=="ERROR")' logs/backend/api.jsonl | jq -r '.event // .logger' | sort | uniq -c | sort -rn | head -20
```

---

## 8. AI subsystem

[backend_ai/backend/app/ai/](backend_ai/backend/app/ai/) — assistant + knowledge base + LoRA training pipeline:
- `routes_ai.py` — HTTP router cho chat/care/training.
- `care_campaign_service.py` — chiến dịch chăm sóc user qua AI.
- `continuous_learning.py` — vòng học liên tục.
- `deferred_queue.py` — queue request AI lùi (rate-limit / cost-saving).
- Knowledge ingestion + LoRA scripts ở [backend_ai/backend/scripts/](backend_ai/backend/scripts/) (`ingest_platform_*`, `build_lora_training_job.py`, `evaluate_ai_training_dataset.py`, `register_ai_model_version.py`).

Module này có lifecycle riêng: `start_ai_care_campaign`, `start_ai_continuous_learning`, `start_deferred_ai_queue` được gọi ở startup `main.py`. Khi sửa AI flow, kiểm tra cả startup + shutdown hooks ở [app/main.py](backend_ai/backend/app/main.py).

---

## 9. Anti-patterns đã biết (đừng lặp)

- **Build frontend trên Windows native** → `ERR_UNSUPPORTED_ESM_URL_SCHEME 'd:'`. Phải build trong container Linux (xem README §4.3).
- **Bind backend `127.0.0.1` trong container** → Docker port-forward không tới được, curl `/health` báo `Empty reply from server`. Set `BACKEND_HOST=0.0.0.0`.
- **Quên copy `ops_telegram_alerts.py` vào image** → `ModuleNotFoundError: ops_telegram_alerts` khi spider-app start. Dockerfile phải có `COPY ops_telegram_alerts.py /app/ops_telegram_alerts.py`. Đã fix ở commit `2cf8257`.
- **Chạy 2 hubbot cùng token** (dev + prod, hoặc 2 dev) → `Conflict: terminated by other getUpdates request`. Tạo bot mới qua @BotFather hoặc dừng instance kia.
- **`PUBLIC_BASE_URL` còn HTTP** → Telegram reject menu button → hubbot fail → fallback "Hệ thống đang xử lý nhiều yêu cầu". Phải tunnel HTTPS (cloudflared/ngrok) trước.
- **Đổi tunnel URL nhưng quên rebuild frontend** → client vẫn gọi URL cũ vì `NEXT_PUBLIC_*` inline tại build time.
- **Sửa schema control-plane mà không sync repo Windows** → command bị reject ở runner hoặc event không parse được ở backend.
- **Gọi API user/Mini App từ Windows runner** → vi phạm contract, runner chỉ được dùng `/api/v2/runner/*`.
- **Dùng PM2 trên Windows native** — `ecosystem.config.js` dự kiến chạy trên Linux host (venv path Unix). Compose là đường duy nhất trên Windows/macOS.
- **`docker compose down -v`** khi đang dev DB có dữ liệu test → mất volume Postgres + Redis dump. Chỉ dùng khi cần reset sạch.

---

## 10. Lệnh thường dùng

```bash
# Trạng thái + log
docker compose ps
docker compose logs -f spider-app
docker compose logs -f hubbot
docker compose logs --tail=50 spider-app

# Restart
docker compose restart hubbot
docker compose up -d --no-deps spider-app
docker compose up -d --force-recreate --no-deps spider-app

# Rebuild image
docker compose build spider-app
docker compose up -d spider-app

# Shell
docker compose exec spider-app bash
docker compose exec db psql -U spider_dev -d spider_dev

# Build frontend (BẮT BUỘC trong container Linux)
docker run --rm \
  -v "$(pwd)/frontend-v2:/app" -w /app \
  -e NEXT_PUBLIC_BACKEND_URL=https://<tunnel> \
  -e NEXT_PUBLIC_API_URL=https://<tunnel> \
  node:20-bookworm-slim \
  bash -c "rm -rf node_modules out .next && npm install --no-audit --no-fund && npm run build"

# Tunnel HTTPS dev
cloudflared tunnel --url http://localhost:8001

# Migration
docker compose exec spider-app bash -lc 'cd /app/backend_ai/backend && alembic upgrade head'
docker compose exec spider-app bash -lc 'cd /app/backend_ai/backend && alembic current'

# Verify
curl -fsS http://127.0.0.1:8001/health
curl -fsS http://127.0.0.1:8001/ready
curl -sS -o /dev/null -w "%{http_code}\n" https://<tunnel>/
```

---

## 11. Troubleshooting nhanh

| Triệu chứng | Nguyên nhân | Fix |
|---|---|---|
| `spider-app` exit `ModuleNotFoundError: ops_telegram_alerts` | Dockerfile thiếu COPY | Đảm bảo `backend_ai/Dockerfile` có `COPY ops_telegram_alerts.py /app/ops_telegram_alerts.py` |
| `init_postgres_schema_failed: relation "runtime_logs" does not exist` | Thứ tự index sai trong `init_pg_schema.py` | `_create_control_plane_scale_indexes(cur)` phải gọi cuối hàm |
| `/health` `Empty reply from server` | Bind `127.0.0.1` trong container | `BACKEND_HOST=0.0.0.0` trong `.env.linux` |
| Bot trả "Hệ thống đang xử lý nhiều yêu cầu" | Generic fallback ở `app/lifecycle/error_handlers.py` | Đọc log hubbot để lấy exception thật, thường là URL Mini App chưa HTTPS |
| Mini App click → trang trắng / 404 | `frontend-v2/out` chưa build hoặc chưa mount | Build lại trong container Linux + restart spider-app |
| `next build` fail `ERR_UNSUPPORTED_ESM_URL_SCHEME 'd:'` | Build trên Windows native | Chỉ build trong container Linux |
| `Conflict: terminated by other getUpdates request` | Token đang được instance khác poll | Dừng instance kia hoặc tạo bot mới |
| `Menu button web app url '...' is invalid: only https links are allowed` | `PUBLIC_BASE_URL` còn HTTP | Tunnel HTTPS + restart hubbot |
| Mini App load nhưng API 401/403 | `BACKEND_API_KEY` lệch giữa hubbot/backend/runner | Đồng bộ key 3 phía |
| Quick tunnel chết → Mini App lỗi | Cloudflare quick tunnel sống theo process + URL random | Dùng cloudflared named tunnel + DNS route, hoặc ngrok reserved domain |

Chi tiết hơn ở [README.md §7](README.md) và [DEPLOY_FRESH_VPS.md §14](DEPLOY_FRESH_VPS.md).

---

## 12. Quy tắc chung khi sửa code

1. **Đừng break wire format command/event** — coordinate với repo Windows runner trước khi đổi.
2. **Đừng ghi secret vào file commit** — kể cả comment/log.
3. **Migration phải idempotent** — Alembic + `init_pg_schema.py` cùng tồn tại, không trùng nhau, không phá thứ tự.
4. **Postgres = truth, Redis = transport** — không lưu state lâu dài chỉ ở Redis.
5. **Mọi I/O ở hubbot phải có timeout + log lỗi** — không để exception rơi tự do làm crash polling loop (theo [hubbot/app/README.md](hubbot/app/README.md)).
6. **Endpoint `/api/v2/runner/*`** chỉ dành cho Windows runner. Đừng route API user qua đó.
7. **Build frontend** trong container Linux. Bao giờ đổi `NEXT_PUBLIC_*` URL → rebuild + force-recreate spider-app.
8. **PM2 path hard-code Linux** — khi sửa `ecosystem.config.js`, đừng giả định layout Windows.
9. **Khi thêm log mới, ưu tiên `logger.info("event_name ...", extra={...})`** thay vì f-string nhúng giá trị (account_id, deployment_id, runner_id, command_id…). `extra={...}` keys tự lên JSON record để `jq` pick được. Bind context vào `contextvars` (`app.core.log_context.bind_log_context`) ở entry point của task thay vì truyền tay xuống các hàm con.

---

## 13. Câu chốt 1 dòng

Spider AI Linux backend = control-plane FastAPI + Telegram bot + Mini App, dùng Postgres làm source of truth và Redis làm transport, điều phối Windows runner fleet thực thi MT5 — KHÔNG trade trực tiếp, KHÔNG đụng terminal MT5, mọi lệnh trade đi qua hợp đồng `RunnerCommand`/`RunnerEvent` xuống runner.
