# `hubbot/app/consumer/` — RabbitMQ (legacy / tuỳ chọn)

## Nhiệm vụ

- Lắng nghe queue **RabbitMQ** (`spider_commands` trong `rabbitmq_commands.py`) và **forward** payload tới backend HTTP (consumer không tự “start bot” trên MT5).
- Nếu không có module `shared.rabbitmq_manager` trong môi trường, consumer **bỏ qua** có log warning (Compose mặc định thường không mount Rabbit consumer path).

## Hành vi vận hành

- Tự reconnect khi mất kết nối (vòng lặp trong consumer).
- Ack message trong context xử lý để tránh mất dấu command.
- Log rõ action để truy vết khi có sự cố.

## Lưu ý an toàn

- Không mở rộng business phức tạp trong consumer — chỉ validate tối thiểu + gọi backend.
- Chỉ route command hợp lệ; dữ liệu thiếu → bỏ qua có log cảnh báo.
- Header tới backend: xem `rabbitmq_commands.py` (`X-From-RabbitMQ-Consumer`, API key theo env).

## Đào tạo

- Đọc `rabbitmq_commands.py` trước khi bật Rabbit trong staging.
- Không nhầm với **Redis command queue** của Windows runner — đó là pipeline backend ↔ runner, không qua hubbot.
