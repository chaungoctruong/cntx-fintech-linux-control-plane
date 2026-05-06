# Deployments SQL

## Nhiệm vụ
- Chứa SQL cho deployment lifecycle: list, check ownership, đếm deployment active theo policy.
- Hỗ trợ truy vấn event ORDER_FILLED theo deployment.

## Lưu ý an toàn
- Các query ownership (`user_id`) là hàng rào bảo mật, không được bỏ.
- LIMIT/ORDER BY phải rõ ràng để tránh kết quả không ổn định.
