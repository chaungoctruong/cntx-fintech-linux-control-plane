# Consumer

## Nhiệm vụ
- Lắng nghe queue RabbitMQ và chuyển command start/stop về backend.

## Hành vi vận hành
- Tự reconnect khi mất kết nối.
- Ack message trong context xử lý để tránh mất dấu command.
- Log rõ action/profile để truy vết khi có sự cố.

## Lưu ý an toàn
- Không mở rộng xử lý business phức tạp trong consumer.
- Chỉ route command hợp lệ; dữ liệu thiếu phải bỏ qua có log cảnh báo.
