# `app/` — lõi FastAPI (control-plane)

Mọi request HTTP (trừ static), worker nền, và wiring lifecycle nằm dưới đây. **Execution plane** (MT5) nằm trên Windows runner — không tìm logic đặt lệnh broker trong `app/` ngoài việc **ghi DB + publish Redis**.

## Bản đồ nhanh (đào tạo: đọc theo thứ tự)

| Thư mục / file | Nhiệm vụ ngắn |
|----------------|---------------|
| **`main.py`** | Tạo FastAPI app, mount router `api/v2`, middleware, lifespan (consumer, reconciler, AI job…). |
| **`settings.py`** | Pydantic Settings — đọc env, default, alias. |
| **`api/v2/`** | Router HTTP theo domain: `accounts`, `deployments`, `runners`, `miniapp`, `wallet`, `admin`, `tradingview_webhook`, `system`, … |
| **`services/`** | Logic nghiệp vụ dày: `control_plane_service`, miniapp, broker legacy, watchdog, GSAlgo state, … |
| **`orchestration/`** | Vòng đời deployment, scheduler slot, policy start/stop, config hot path. |
| **`repositories/`** | Truy cập Postgres; SQL tách theo domain trong **`repositories/control_plane/sql/`** — mục lục [sql/README.md](repositories/control_plane/sql/README.md). |
| **`events/`** | Router lệnh runner → Redis, ingest heartbeat/event, reconciler delivery, webhook, consumer stream. |
| **`infra/redis_streams.py`** | Publish lệnh / stream (transport Redis). |
| **`runner/`** | Hợp đồng Windows: prompt tích hợp, `control_plane_client` (HTTP ngắn), `queue_consumer` (stub dequeue Redis), `protocol`. |
| **`models/`** | SQLAlchemy models + hằng trạng thái domain. |
| **`schemas/`** | Pydantic request/response (contract API + runner payload). |
| **`core/`** | Logging, context, Redis client, rate limit, internal auth. |
| **`monitoring/`** | Metrics / reconciler quan sát control-plane. |
| **`risk/`** | Quota, policy, circuit breaker gắn orchestration. |
| **`ai/`** | Assistant, route AI, pipeline (không nhầm với điều phối MT5). |
| **`providers/`** | Adapter LLM / dịch vụ ngoài. |
| **`bot_catalog/`** | Loader catalog bot MT5 từ disk/repo bot-trading. |
| **`security.py`** | Tiện ích mã hóa / ràng buộc an toàn ứng dụng. |
| **`store.py`** | Store process-local / DB access pattern dùng chung. |

## Luồng xử lý chuẩn (1 request)

1. Client → `api/v2/*` (auth, validate schema).
2. `services/*` hoặc `orchestration/*` quyết định nghiệp vụ.
3. `repositories/*` ghi/đọc Postgres (SQL trong `repositories/control_plane/sql/`).
4. `events/*` publish Redis / consumer / reconciler khi cần.
5. Response schema từ `schemas/`.

## Transport runner (để nhân viên không nhầm)

- **Lệnh** (`START_BOT`, `STOP_BOT`, `PLACE_ORDER`, …): Linux → **Redis list** per `runner_id`. Windows **`RUNNER_TRANSPORT=redis_queue`**.
- **HTTP** (`/runner/register`, `/heartbeat`, `/events`, `/commands/{id}/delivery`, package, verify): request ngắn, không thay Redis cho lệnh.

## Hành vi bắt buộc khi sửa code

- Không đổi tuple `delivery_status` / lifecycle deployment nếu chưa có migration + review.
- SQL: luôn bind param; không nối chuỗi user input vào SQL.
- Không log secret (token, password, `BACKEND_API_KEY`).
- Worker: idempotent, chống poison message.

## Đào tạo theo giai đoạn

1. Đọc `main.py` + `settings.py` + `api/v2/runners.py` (contract runner HTTP).
2. Đọc `events/command_router.py` + `infra/redis_streams.py` (đường lệnh Redis).
3. Đọc README từng nhóm SQL (domain DB).
4. Debug: JSONL backend theo `request_id` / `event` (xem `CLAUDE.md` monorepo).

## Checklist task mới

- Thuộc domain API / service / repo / event / risk / monitoring?
- Source of truth: Postgres row nào? Redis chỉ transport?
- Có ảnh hưởng Windows runner contract (`schemas/control_plane.py`) không? Nếu có → đồng bộ repo Windows.
