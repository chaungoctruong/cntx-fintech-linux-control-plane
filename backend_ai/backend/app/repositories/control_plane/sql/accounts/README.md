# Accounts SQL

## Nhiệm vụ
- Chứa SQL cho vòng đời broker account: đọc/cập nhật risk policy, tính PnL ngày, soft-delete và scrub credentials.
- Hỗ trợ các truy vấn tổng hợp cho policy và circuit breaker.

## Lưu ý an toàn
- Giữ thứ tự tham số `%s` đúng với code Python gọi `cur.execute(...)`.
- Không chèn giá trị trực tiếp vào SQL; luôn binding qua params.
