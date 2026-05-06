# Callback Router

## Nhiệm vụ
- Nhận callback từ inline keyboard và điều phối đúng luồng xử lý.
- Bảo vệ UX bằng cooldown/dedup, tránh spam thao tác liên tiếp.

## Hành vi hiện tại
- Callback điều khiển bot cũ trong Telegram được chuyển hướng sang Mini App.
- Callback không hợp lệ hoặc cũ vẫn trả thông điệp hướng dẫn rõ ràng.

## Lưu ý khi chỉnh sửa
- Không bỏ dedup/cooldown nếu chưa có cơ chế thay thế tương đương.
- Ưu tiên thông điệp ngắn, dễ hiểu, tránh gây hiểu nhầm trạng thái bot.
