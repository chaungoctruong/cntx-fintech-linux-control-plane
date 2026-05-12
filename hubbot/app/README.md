# `hubbot/app/` — Lớp ứng dụng Telegram

Toàn bộ logic nghiệp vụ tách khỏi `main.py`: command, callback, message, gọi backend, consumer queue, lifecycle.

## Cấu trúc thư mục

| Path | Việc làm |
|------|----------|
| **`commands/`** | Slash commands — xem [commands/README.md](commands/README.md). |
| **`callback/`** | Router `CallbackQuery` — xem [callback/README.md](callback/README.md). |
| **`api/`** | Client HTTP tới backend — xem [api/README.md](api/README.md). |
| **`consumer/`** | RabbitMQ consumer — xem [consumer/README.md](consumer/README.md). |
| **`lifecycle/`** | Lock, shutdown, hooks, logging update — xem [lifecycle/README.md](lifecycle/README.md). |

## File gốc trong `app/` (inventory)

| File | Việc làm |
|------|----------|
| **`config.py`** | Env-driven settings, timeout, feature flags. |
| **`message.py`** | Handler tin nhắn / `web_app_data`. |
| **`keyboards.py`** | Inline keyboard, URL Mini App. |
| **`formatters.py`** | Escape/format text trả user. |
| **`state.py`** | State session nhẹ. |
| **`logging_config.py`** | Cấu hình logger service `hubbot`. |
| **`log_context.py`** | Context cho log có `request_id` / trace. |
| **`error_log.py`** | Tiện ích log lỗi có cấu trúc. |
| **`debug.py`** | Debug radar (theo env, không dùng cho prod logic). |

## Hành vi bắt buộc

- Mọi I/O (HTTP, queue) phải có **timeout** và **log lỗi** (không để exception làm sập vòng polling).
- Trả thông điệp thân thiện khi backend lỗi hoặc quá tải.
- Không gọi `httpx` trực tiếp từ command/callback/message — dùng `api/`.

## Đào tạo

1. Đọc `config.py` + `api/client.py`.
2. Trace một luồng: `commands/start.py` → `api/` → backend.
3. Đọc `lifecycle/error_handlers.py` để hiểu fallback user-facing.
