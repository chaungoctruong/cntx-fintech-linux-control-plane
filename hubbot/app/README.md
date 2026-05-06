# App Layer

## Nhiệm vụ
- Chứa toàn bộ logic nghiệp vụ của hubbot, tách khỏi `main.py`.
- Giữ code theo module rõ ràng để dễ debug và dễ train nhân sự mới.

## Cấu trúc
- `commands/`: lệnh Telegram.
- `callback/`: xử lý callback query.
- `message.py`: xử lý tin nhắn text/web_app_data.
- `api/`: gọi backend và các cơ chế cache/dedup.
- `consumer/`: xử lý command từ RabbitMQ.
- `lifecycle/`: lock instance và cleanup shutdown.
- `state.py`: state nhẹ theo context người dùng.

## Hành vi bắt buộc
- Mọi thao tác I/O (HTTP, queue) phải có timeout và log lỗi.
- Trả thông điệp thân thiện cho user khi backend lỗi hoặc quá tải.
- Không để exception rơi tự do làm crash polling loop.
