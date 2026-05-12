# `backend_ai/` — Docker build context + backend Python

Thư mục này là **một gói triển khai**: image FastAPI (`Dockerfile`) và toàn bộ mã backend trong **`backend/`**.

## Ai đọc file này?

- Dev/Ops cần biết **chỗ build image** vs **chỗ chạy Alembic / script**.
- Nhân viên mới: đi thẳng vào **[backend/README.md](backend/README.md)** để hiểu vai trò control-plane, env, checklist.

## Cấu trúc tối thiểu

| Đường dẫn | Nhiệm vụ |
|-----------|----------|
| **`Dockerfile`** | Build image service `spider-app` (Compose/PM2 mount code từ monorepo). |
| **`backend/`** | FastAPI app, Alembic, script, static — xem [backend/README.md](backend/README.md). |

## Không nhầm lẫn

- **`backend_ai/`** = context Docker + backend; **không** chứa hubbot hay frontend Next.js (nằm cùng cấp monorepo).
- Lệnh điều khiển bot xuống Windows runner: **Redis queue** (`RUNNER_TRANSPORT=redis_queue` trên Windows). HTTP runner chỉ cho **register / heartbeat / events / delivery / package / verify**.
