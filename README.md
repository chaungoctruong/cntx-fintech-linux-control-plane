# Spider AI — Hướng dẫn chạy project A→Z

Monorepo gồm:

- **`backend_ai/backend/`** — FastAPI control-plane (Postgres + Redis), expose `/health`, `/ready`, `/api/v2/*`. Cũng serve Mini App static (Next.js export).
- **`hubbot/`** — Telegram bot (python-telegram-bot, long-poll), gọi backend qua HTTP.
- **`frontend-v2/`** — Next.js 14 Mini App, build static export → backend mount tại `/_next` và serve HTML qua catch-all.
- **`runner/`** — Windows MT5 runner (không nằm trong compose, deploy riêng).
- **`docker-compose.yml`** — local/dev: Postgres + Redis + spider-app + hubbot.
- **`ecosystem.config.js`** — PM2 cho production-style trên Linux host (`/root/spider-ai/...`).

## Tài liệu vận hành nội bộ

Khi chuẩn bị chạy theo hướng test/product, nhân viên nên đọc theo thứ tự này:

| Tài liệu | Khi nào đọc |
|---|---|
| [README.md](README.md) | Tổng quan repo, Docker Compose, health check, lệnh vận hành chung |
| [backend_ai/backend/README.md](backend_ai/backend/README.md) | Backend Linux control-plane, DB/Redis, catalog bot, runner heartbeat |
| [frontend-v2/README.md](frontend-v2/README.md) | Frontend Mini App, biến `NEXT_PUBLIC_BACKEND_URL`, build/rebuild |
| [bot-trading/README.md](bot-trading/README.md) | Registry bot package trên Linux, contract `gsalgovip`, quy tắc không chứa secret |
| [runner/README.md](runner/README.md) | Schema/chia sẻ hợp đồng với Windows runner |
| [config/README.md](config/README.md) | Nginx/config handoff và quyền sở hữu file cấu hình |

Quy ước quan trọng:

- Repo root `.env` là file runtime chính cho Docker Compose trên Linux.
- `backend_ai/backend/.env` chỉ dùng khi chạy backend trực tiếp ngoài compose.
- `frontend-v2/.env` chỉ dùng cho frontend khi build/chạy riêng.
- Windows runner dùng file `.env` riêng ở máy Windows, không dùng trực tiếp DB Linux.
- Không commit hoặc dán secret thật vào tài liệu, issue, log hay chat.

Tài liệu này tập trung **luồng A — Docker Compose** vì đây là cách nhanh nhất đưa toàn bộ chạy trên 1 máy. Luồng PM2 được nhắc cuối file.

---

## 1. Yêu cầu hệ thống

| Bắt buộc | Phiên bản tối thiểu |
|---|---|
| Docker Desktop (Windows/macOS) hoặc Docker Engine + plugin compose v2 (Linux) | Docker 24+, compose v2 |
| Node.js + npm — chỉ cần khi build frontend | Node 20 LTS |

| Tuỳ chọn | Khi nào cần |
|---|---|
| `cloudflared` hoặc `ngrok` | Khi muốn nút Mini App trong Telegram hoạt động (Telegram bắt buộc URL HTTPS cho `web_app`) |
| `psql` client | Truy vấn DB từ host khi debug |

Tất cả service chạy trong container, host **không cần** cài Postgres/Redis/Python sẵn cho luồng compose.

---

## 2. Chuẩn bị một lần

### 2.1 Token Telegram bot

Tạo bot mới qua [@BotFather](https://t.me/BotFather) → `/newbot`. Lấy token dạng `123456:ABC-DEF...`.

> ⚠️ **Không dùng token bot prod cho dev.** Telegram chỉ cho 1 consumer/`getUpdates` cùng token — chạy hubbot local cùng token với hubbot prod sẽ làm cả hai trả `Conflict: terminated by other getUpdates request`.

### 2.2 File `.env` (compose runtime)

`docker-compose.yml` đọc trực tiếp `.env`. File này đã được `.gitignore`
qua pattern `.env*`, nên có thể chứa secret local/test và không được commit.

Tạo `.env` ở repo root:

```env
# Bind uvicorn ra 0.0.0.0 để Docker port-forward 8001 đến được container.
BACKEND_HOST=0.0.0.0
API_HOST=0.0.0.0

# Token bot Telegram cho compose local.
TELEGRAM_BOT_TOKEN=<paste-token-bot-test-cua-ban>

# URL công khai trỏ về backend. Khi chạy server test/product, dùng IP/domain của Linux.
# PUBLIC_BASE_URL=http://<linux-ip-hoac-domain>:8001

# URL nội bộ để container hubbot gọi backend trong Docker network.
# Giữ giá trị này khi chạy bằng Docker Compose.
# BACKEND_URL=http://spider-app:8001

# URL public để Windows runner đăng ký/heartbeat về Linux control-plane.
# RUNNER_CONTROL_PLANE_URL=http://<linux-ip-hoac-domain>:8001
```

---

## 3. Khởi chạy nhanh (Docker Compose)

```bash
# Tại repo root
docker compose up -d --build
```

Lần đầu mất 5–15 phút (pull image, cài Python deps). Các lần sau dùng cache, vài giây.

Xác nhận trạng thái:

```bash
docker compose ps
```

Kết quả mong đợi: `db`, `redis`, `spider-app`, `hubbot` đều `Up`. `spider-app` map `0.0.0.0:8001->8001/tcp`.

Verify backend:

```bash
curl -fsS http://127.0.0.1:8001/health | jq .ok    # true
curl -fsS http://127.0.0.1:8001/ready  | jq .ok    # true
```

Lúc này:
- Bot đã long-poll Telegram, gõ `/ping` cho bot trên Telegram là phải có phản hồi.
- Mini App chưa hoạt động vì `PUBLIC_BASE_URL` còn HTTP — xem mục 4.

---

## 4. Mini App: public URL + build frontend

Telegram chặn mọi `web_app` URL không phải HTTPS. Khi chạy product thật, dùng domain + TLS qua Nginx/Cloudflare và set `PUBLIC_BASE_URL=https://<domain>`. Khi chỉ test backend/runner bằng IP public, HTTP vẫn đủ cho runner register/heartbeat nhưng chưa đủ cho Mini App Telegram.

### 4.1 Mở tunnel HTTPS khi chạy local

Cách nhanh nhất là **Cloudflare Quick Tunnel** (không cần đăng ký, URL random):

```bash
cloudflared tunnel --url http://localhost:8001
```

Cloudflared in ra dòng dạng `https://<adj>-<noun>-<adj>-<noun>.trycloudflare.com` — copy URL này. Để tunnel chạy ở terminal riêng (URL chết khi process tắt).

> 🔁 **URL random sẽ đổi mỗi lần restart tunnel.** Stable hơn: `cloudflared tunnel create <name>` + DNS route Cloudflare (cần Cloudflare account + domain). Hoặc `ngrok` với reserved domain (paid).

### 4.2 Update `.env`

Mở `.env`, set URL công khai cho Mini App và runner. Với Docker Compose, giữ `BACKEND_URL=http://spider-app:8001` để hubbot/container gọi backend qua mạng nội bộ:

```env
PUBLIC_BASE_URL=https://<your-tunnel>.trycloudflare.com
RUNNER_CONTROL_PLANE_URL=https://<your-tunnel>.trycloudflare.com
BACKEND_URL=http://spider-app:8001
```

### 4.3 Build frontend Next.js

Frontend cấu hình `output: "export"` — `next build` sinh `frontend-v2/out/`. Backend đã sẵn sàng mount thư mục này.

> ⚠️ **Bắt buộc build trong container Linux.** Build `next build` trên Windows native fail với `ERR_UNSUPPORTED_ESM_URL_SCHEME ... Received protocol 'd:'` (bug Node ESM với absolute path có drive letter).

```bash
docker run --rm \
  -v "D:/Spider/linux-root-backend-hubot-v1/frontend-v2:/app" \
  -w /app \
  -e NEXT_PUBLIC_BACKEND_URL=https://<your-tunnel>.trycloudflare.com \
  -e NEXT_PUBLIC_API_URL=https://<your-tunnel>.trycloudflare.com \
  node:20-bookworm-slim \
  bash -c "rm -rf node_modules out .next && npm install --no-audit --no-fund && npm run build"
```

Đổi đường dẫn host cho phù hợp máy bạn. Build xong có 8 route static dưới `frontend-v2/out/`.

> 📌 `NEXT_PUBLIC_*` được **inline tại build time** vào client bundle. Mỗi lần đổi URL tunnel phải build lại frontend.

### 4.4 Apply

`docker-compose.yml` đã có volume mount `./frontend-v2/out:/app/frontend-v2/out:ro`. Chỉ cần restart spider-app + hubbot để đọc `.env` mới:

```bash
docker compose up -d spider-app hubbot
```

Verify Mini App home (qua public URL):

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://<your-tunnel>.trycloudflare.com/
# → 200
```

Trên Telegram: `/start` lại với bot — tin nhắn sẽ kèm nút Mini App, click vào load được trang.

Trong log hubbot phải thấy:

```
Telegram menu button configured for Mini App home.
```

Nếu vẫn thấy `Menu button web app url '...' is invalid: only https links are allowed` → `.env` chưa được load (kiểm tra path) hoặc chưa restart hubbot.

---

## 5. Lệnh thường dùng

```bash
# Trạng thái + log
docker compose ps
docker compose logs -f spider-app          # tail backend log
docker compose logs -f hubbot              # tail hubbot log
docker compose logs --tail=50 spider-app   # 50 dòng cuối

# Restart 1 service
docker compose restart hubbot
docker compose up -d --no-deps spider-app  # restart không kéo deps lên lại

# Rebuild image sau khi sửa Dockerfile
docker compose build spider-app
docker compose up -d spider-app

# Vào shell container
docker compose exec spider-app bash
docker compose exec db psql -U spider_dev -d spider_dev

# Tắt
docker compose down              # giữ volume db/redis
docker compose down -v           # XOÁ volume → mất dữ liệu DB
```

### Migration thủ công

Backend tự chạy `init_postgres_schema()` ở startup (idempotent). Chỉ cần manual khi muốn áp Alembic revision mới:

```bash
docker compose exec spider-app bash -lc 'cd /app/backend_ai/backend && alembic upgrade head'
docker compose exec spider-app bash -lc 'cd /app/backend_ai/backend && alembic current'
```

Quy ước an toàn theo [backend_ai/backend/migrations/README.md](backend_ai/backend/migrations/README.md):
- DB **mới**: `alembic upgrade head`
- DB **đang chạy với schema từ `init_pg_schema.py`**: `alembic stamp head` (đánh dấu, không thực thi)

---

## 6. Cấu trúc env files

| File | Vai trò | Có commit không |
|---|---|---|
| `.env` | Runtime Docker Compose, chứa secret local/test | ❌ gitignored (`.env*`) |
| `backend_ai/backend/.env` | Production thực — KHÔNG commit | ❌ gitignored |
| `frontend-v2/.env` | Runtime frontend local/test | ❌ gitignored (`.env*`) |

Biến quan trọng nhất ở compose layer:

| Biến | Ý nghĩa |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token bot — bắt buộc để hubbot khởi động |
| `BACKEND_HOST` | Phải `0.0.0.0` trong container để port-forward Docker tới được |
| `PUBLIC_BASE_URL` | URL công khai — Mini App URL build từ đây |
| `BACKEND_URL` | URL nội bộ để hubbot/container gọi backend. Với Docker Compose giữ `http://spider-app:8001` |
| `RUNNER_CONTROL_PLANE_URL` | URL public để Windows runner gọi register/heartbeat/job API |
| `BACKEND_API_KEY` | Khoá hubbot ↔ backend, phải khớp 2 phía |
| `REDIS_URL`, `BOT_COMMAND_QUEUE_REDIS_URL` | DB index 0 = prod, dev có thể đổi `/1` |
| `DRY_RUN` | `1` = không gửi lệnh thật xuống MT5 |

---

## 7. Troubleshooting

### `spider-app` exit(1) — `ModuleNotFoundError: No module named 'ops_telegram_alerts'`

[backend_ai/Dockerfile](backend_ai/Dockerfile) phải có dòng `COPY ops_telegram_alerts.py /app/ops_telegram_alerts.py` (file ở repo root, code import top-level).

### `spider-app` exit(1) — `init_postgres_schema_failed: relation "runtime_logs" does not exist`

Bug thứ tự trong [backend_ai/backend/init_pg_schema.py](backend_ai/backend/init_pg_schema.py): hàm `_create_control_plane_scale_indexes(cur)` phải gọi **sau** khi `runtime_logs` đã được tạo (cuối hàm, không phải giữa hàm).

### Curl `/health` trả `Empty reply from server` mặc dù container Up

Uvicorn bind `127.0.0.1` trong container thay vì `0.0.0.0`. Set `BACKEND_HOST=0.0.0.0` trong `.env`, restart spider-app.

### Bot trả "Hệ thống đang xử lý nhiều yêu cầu"

Đây là **generic error fallback** ở [hubbot/app/lifecycle/error_handlers.py](hubbot/app/lifecycle/error_handlers.py) — bất kỳ exception nào trong handler đều trả câu này. Xem log hubbot để biết exception thật:

```bash
docker compose logs --tail=100 hubbot | grep -A 5 ERROR
```

Nguyên nhân hay gặp: URL Mini App chưa HTTPS → Telegram reject → hubbot fail → fallback. Fix bằng tunnel ở mục 4.

### Mini App click mở ra trang trắng / 404

`frontend-v2/out` chưa build hoặc chưa mount. Kiểm tra:

```bash
docker compose exec spider-app ls -la /app/frontend-v2/out/_next | head
```

Nếu trống → chạy lại lệnh build ở mục 4.3.

### `next build` trên Windows fail `ERR_UNSUPPORTED_ESM_URL_SCHEME ... Received protocol 'd:'`

Bug Node ESM loader với Windows path. **Phải build trong container Linux** (mục 4.3). Đừng `cd frontend-v2 && npm run build` trên PowerShell/CMD.

### Hubbot vẫn báo `Conflict: terminated by other getUpdates request`

Token đang được instance khác poll (hubbot prod, máy đồng nghiệp, hoặc instance bot trước chưa thoát hẳn). Tạo bot mới qua @BotFather hoặc dừng instance kia.

### Tunnel cloudflared chết, Mini App lại lỗi

Quick Tunnel sống theo process. Nếu dùng quen, cân nhắc:
- `cloudflared service install` + Cloudflare named tunnel + DNS route → URL cố định.
- Hoặc dùng `ngrok http 8001` với reserved domain (paid plan).

---

## 8. Reset & cleanup

```bash
# Tắt + xoá container, giữ volume
docker compose down

# Tắt + xoá volume (mất toàn bộ DB Postgres + Redis dump)
docker compose down -v

# Xoá image build local
docker image rm linux-root-backend-hubot-v1-spider-app linux-root-backend-hubot-v1-hubbot

# Reset frontend artifacts
rm -rf frontend-v2/node_modules frontend-v2/out frontend-v2/.next
```

---

## 9. Kiến trúc deploy production (ngoài phạm vi compose)

Production dùng PM2 trên Linux host theo [ecosystem.config.js](ecosystem.config.js):

- `spider-backend`: cwd `/root/spider-ai/backend_ai/backend`, venv `venv/bin/python3`, script `scripts/run_api.py`. 2 instance, port = `API_PORT_BASE` (8002) + `INSTANCE_ID` → 8002 và 8003.
- `spider-hubbot`: cwd `/root/spider-ai/hubbot`, venv `venv_hub/bin/python3`, script `main.py`. 1 instance.

Các path được hard-code cho Linux host — **không** chạy được trên Windows native. Compose là cách duy nhất để dev/test trên Windows hoặc macOS.

Runtime env nằm trong `backend_ai/backend/.env` khi chạy trực tiếp trên host.

---

## 10. Tham khảo nhanh

| Endpoint | Mục đích |
|---|---|
| `GET /health` | Liveness chi tiết (DB, Redis, runtime, AI) |
| `GET /ready` | Readiness gọn cho load balancer |
| `GET /api/v2/system/healthz` | Legacy nginx probe |
| `GET /` (qua public URL) | Mini App home (Next.js export) |

| File | Tài liệu chuyên đề |
|---|---|
| [backend_ai/backend/migrations/README.md](backend_ai/backend/migrations/README.md) | Quy trình Alembic migration |
| [hubbot/README.md](hubbot/README.md) | Cấu trúc hubbot |
| [runner/README.md](runner/README.md) | MT5 runner Windows |
| [config/README.md](config/README.md) | Cấu hình chung |
