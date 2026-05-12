# `hubbot/app/commands/` — Slash commands

## File (inventory)

| File | Lệnh / vai trò |
|------|----------------|
| **`start.py`** | `/start` — onboarding, link Mini App, luồng chào mừng. |
| **`ping.py`** | `/ping` — kiểm tra bot còn sống, latency thô. |
| **`server_status.py`** | `/trangthai`, `/sys` — **chỉ dành cho dev**: CPU/RAM host, giới hạn theo `DEV_CHAT_ID`. |

Đăng ký handler trong **`hubbot/main.py`** (`CommandHandler`).

## Nguyên tắc triển khai

- Mỗi file một nhóm lệnh rõ trách nhiệm.
- Trả lời nhanh; gọi backend chỉ khi cần (tránh spam API).
- Thêm lệnh mới: đăng ký tại `main.py`, kiểm tra trùng tên với bot khác trong cùng chat.

## Đào tạo

- Nhân viên mới: mở `start.py` làm mẫu pattern reply + gọi `api/` nếu cần.
- Không nhét logic nặng vào command — chuyển xuống `api/` hoặc service backend.
