# Lifecycle

## Nhiệm vụ
- Quản lý vòng đời tiến trình: khóa single-instance, cleanup khi shutdown.

## Mục tiêu
- Ngăn nhiều instance polling cùng token Telegram.
- Đảm bảo task nền và tài nguyên được đóng sạch khi dừng bot.

## Quy tắc chỉnh sửa
- Không bỏ lock đơn instance nếu chưa có cơ chế đồng bộ thay thế.
- Bất kỳ thay đổi shutdown nào cũng phải ưu tiên tính ổn định và idempotent.
