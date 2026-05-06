# Billing SQL

## Nhiệm vụ
- Chứa SQL liên quan subscription của user.
- Hiện tại tập trung truy vấn lấy subscription active mới nhất.

## Lưu ý an toàn
- Ưu tiên truy vấn có thứ tự sắp xếp rõ ràng (`updated_at DESC, id DESC`).
- Không đổi nghĩa business khi refactor SQL.
