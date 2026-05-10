# Hubbot

## Mục tiêu
- Vận hành bot Telegram cho CNTx Labs theo hướng Mini App-first.
- Đảm bảo luồng chat ổn định: nhận lệnh, gọi backend, phản hồi rõ ràng, có log và cảnh báo vận hành.

## Kiến trúc chính
- `main.py`: bootstrap ứng dụng Telegram, đăng ký handler, chạy polling hoặc webhook, lock single-instance.
- `app/config.py`: cấu hình từ env, giới hạn timeout/concurrency/cooldown.
- `app/commands/`: các lệnh slash như `/start`, `/ping`, `/trangthai`.
- `app/callback/`: router callback; điều hướng thao tác cũ về Mini App.
- `app/message.py`: xử lý tin nhắn thường (claim token, AI chat).
- `app/api/`: client gọi backend, cache ngắn, dedup callback/message.
- `app/consumer/`: consumer RabbitMQ để xử lý command start/stop từ hàng đợi.
- `app/lifecycle/`: shutdown sạch và khóa 1 instance chạy.

## Nguyên tắc an toàn khi sửa
- Không đổi hành vi người dùng nếu chưa có yêu cầu nghiệp vụ rõ ràng.
- Ưu tiên tách hàm nhỏ, đặt tên rõ, không nhồi thêm logic vào `main.py`.
- Mọi call backend phải đi qua lớp `app/api/` để giữ chuẩn timeout/retry/log.
- Khi thêm handler mới, phải xét thứ tự handler và tránh chặn flow hiện có.

## Quy trình verify tối thiểu
- Chạy kiểm tra cú pháp Python cho file vừa sửa.
- Test thủ công các luồng chính: `/start`, callback menu, claim token, AI chat.
- Quan sát log khởi động, log lỗi và cảnh báo ops để chắc chắn không mất tín hiệu giám sát.

## Vận hành Telegram webhook
- Chỉ bật `TELEGRAM_USE_WEBHOOK=1` khi public URL `/telegram/webhook` đã route được về `hubbot` port `8081`.
- Các biến cần có trong `.env`: `TELEGRAM_WEBHOOK_URL`, `TELEGRAM_WEBHOOK_LISTEN`, `TELEGRAM_WEBHOOK_PORT`, `TELEGRAM_WEBHOOK_PATH`, `TELEGRAM_WEBHOOK_SECRET_TOKEN`.
- `TELEGRAM_WEBHOOK_SECRET_TOKEN` là secret vận hành, không ghi vào README, log, commit hoặc chat.
- Nếu public URL còn trả `404`, giữ polling để bot không mất update Telegram.
