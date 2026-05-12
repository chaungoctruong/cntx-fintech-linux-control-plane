# `hubbot/app/lifecycle/` — Vòng đời process

## Nhiệm vụ

- Khóa **single-instance** (một token chỉ một `getUpdates`).
- **Shutdown** sạch: đóng task nền, giải phóng lock.
- **Runtime hooks** sau khi bot start (menu Mini App, v.v. khi env cho phép).
- **Logging** mỗi update (pre/post handler) và **error handlers** thống nhất.
- **Ops alerts** (Telegram phụ) khi lỗi nghiêm trọng — xem `alerts.py`.

## File (inventory)

| File | Việc làm |
|------|----------|
| **`single_instance.py`** | File lock / fcntl để chống chạy trùng process. |
| **`shutdown.py`** | `on_shutdown` — cleanup an toàn. |
| **`runtime.py`** | `post_init` / hooks sau khi application sẵn sàng. |
| **`handler_logger.py`** | Log mỗi update: handler, `elapsed_ms`, context. |
| **`error_handlers.py`** | Bắt exception, fallback message user, alert ops. |
| **`alerts.py`** | Gửi cảnh báo vận hành qua bot hệ thống (theo config). |

## Quy tắc chỉnh sửa

- Không bỏ lock đơn instance nếu chưa có cơ chế đồng bộ thay thế.
- Shutdown phải **idempotent** (gọi lại không hỏng tài nguyên).
