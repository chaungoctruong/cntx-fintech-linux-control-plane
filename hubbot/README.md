# Hubbot — Telegram bot (Spider AI)

Bot Telegram **long-poll** mặc định: nhận update từ Telegram, gọi **FastAPI control-plane** qua HTTP với header `X-Backend-Api-Key`, phản hồi user và (khi bật) đồng bộ menu Mini App.

**Đọc tiếp:** [app/README.md](app/README.md) để biết cây module trong `app/`.

## Entry & cấu hình

| File | Việc làm |
|------|----------|
| **`main.py`** | `load_dotenv`, build `Application`, đăng ký handler, `run_polling` hoặc webhook, lock single-instance. |
| **`requirements.txt`** | Dependency Python riêng hubbot (tách khỏi backend). |
| **`app/config.py`** | Đọc env: token, `BACKEND_URL`, timeout, webhook, cooldown, feature flag. |

## Kiến trúc `app/`

| Thư mục / file | Việc làm |
|----------------|----------|
| **`app/commands/`** | Slash command: `/start`, `/ping`, `/trangthai` (dev — xem `server_status.py`). |
| **`app/callback/`** | `CallbackQuery` — điều hướng thao tác cũ về Mini App, dedup/cooldown. |
| **`app/message.py`** | Tin nhắn text, `web_app_data`, luồng token / AI chat. |
| **`app/keyboards.py`**, **`app/formatters.py`** | UI Telegram (inline keyboard, escape HTML). |
| **`app/api/`** | Client backend: timeout, retry, dedup — **mọi HTTP tới API nên qua đây**. |
| **`app/consumer/`** | Consumer **RabbitMQ** (khi bật): command từ queue nội bộ → xử lý / forward. |
| **`app/lifecycle/`** | Single-instance lock, shutdown, runtime hooks, handler logging, error handlers, ops alert. |
| **`app/state.py`** | State nhẹ theo context người dùng. |
| **`app/logging_config.py`**, **`app/log_context.py`**, **`app/error_log.py`**, **`app/debug.py`** | Log chuẩn + debug radar (theo env). |

## Nguyên tắc an toàn khi sửa

- Không đổi hành vi người dùng nếu chưa có yêu cầu nghiệp vụ rõ ràng.
- Mọi call backend qua lớp `app/api/` để giữ timeout/retry/log.
- Thêm handler mới: kiểm tra thứ tự group handler, tránh chặn flow hiện có.
- **Không** chạy hai process cùng `TELEGRAM_BOT_TOKEN` (Telegram trả conflict).

## Webhook (tuỳ chọn)

- Chỉ bật khi public URL route được tới hubbot (port/path đúng trong env).
- Biến: `TELEGRAM_USE_WEBHOOK`, `TELEGRAM_WEBHOOK_URL`, `TELEGRAM_WEBHOOK_LISTEN`, `TELEGRAM_WEBHOOK_PORT`, `TELEGRAM_WEBHOOK_PATH`, `TELEGRAM_WEBHOOK_SECRET_TOKEN`.
- `TELEGRAM_WEBHOOK_SECRET_TOKEN` không ghi vào README/log/commit.

## Verify tối thiểu sau khi sửa

- Syntax Python cho file vừa sửa.
- Thủ công: `/start`, callback menu, nhận token (nếu có flow), AI chat (nếu bật).
- Log khởi động + JSONL hubbot — không mất cảnh báo ops.
