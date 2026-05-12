# Backend Linux (Control-Plane) — `backend_ai/backend/`

FastAPI **Spider AI control-plane**: user, account, deployment, lệnh runner, Mini App API, TradingView fan-out, AI phụ trợ. **Không** chạy MT5, **không** `OrderSend` — thực thi nằm ở Windows runner.

## Đọc tiếp theo thứ tự (onboarding nhân viên)

1. **[app/README.md](app/README.md)** — cây `app/`, từng thư mục con làm gì, file gốc (`main.py`, `settings.py`).
2. **[migrations/README.md](migrations/README.md)** — Alembic vs `init_pg_schema.py`.
3. **[scripts/README.md](scripts/README.md)** — script vận hành / smoke / AI (không nhầm với code API).
4. SQL theo domain: **`app/repositories/control_plane/sql/README.md`** (mục lục) + từng `*/README.md`.

## Vai trò (tách bạch)

| Tầng | Linux backend làm gì | Không làm gì |
|------|----------------------|--------------|
| **Bật/tắt deployment** | Ghi Postgres, tạo `START_BOT` / `STOP_BOT`, policy orchestration | Không bật terminal MT5 |
| **Lệnh tới bot MT5** | Publish envelope lên **Redis** (`mt5:runner:{RUNNER_ID}:commands`) + stream audit | Không nói chuyện broker |
| **Runner HTTP** | Nhận `POST /api/v2/runner/register`, `heartbeat`, `events`, `.../delivery`, package, verify | **Không** có HTTP long-poll để “lấy lệnh”; lệnh chỉ qua Redis |

## Env (tóm tắt)

| File | Khi nào |
|------|---------|
| **`../../.env`** (repo root) | Docker Compose — **file chính** khi `docker compose up`. |
| **`.env`** (cùng thư mục `backend/`) | Chạy `uvicorn`/script trực tiếp trên host, không qua Compose. |
| **`../../frontend-v2/.env`** | Build Mini App (`NEXT_PUBLIC_*` inline lúc build). |

Không commit `.env`. Không dán secret vào chat/README.

## Checklist chỉ đọc (không trade live)

```bash
curl -fsS http://127.0.0.1:8001/ready
curl -fsS http://127.0.0.1:8001/api/v2/system/healthz
docker compose ps
docker compose logs --tail=100 spider-app
```

- `/ready` → Postgres + Redis (và các check đã wire).
- Runner online: xem API ops hoặc DB `runner_nodes` / log `runner.register` (theo runbook dự án).

## Ranh giới an toàn (vận hành)

- Không gửi `START_BOT` / `STOP_BOT` / `PLACE_ORDER` production khi chưa có kế hoạch.
- Mọi thay đổi schema DB: **Alembic** + review; xem [migrations/README.md](migrations/README.md).

## Liên kết monorepo

- Hubbot, frontend, compose: README gốc repo (`../../README.md`) và `CLAUDE.md`.
