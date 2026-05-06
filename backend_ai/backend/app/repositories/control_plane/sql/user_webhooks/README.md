# User Webhooks SQL

## Nhiệm vụ
- Chứa SQL CRUD webhook của user và truy vấn danh sách cho worker delivery.
- Hỗ trợ list có/không có secret phục vụ các use-case khác nhau.

## Lưu ý an toàn
- Secret chỉ trả về khi dùng query có chủ đích (`with_secret`).
- Giữ ràng buộc `user_id` trong mọi thao tác để tránh truy cập chéo tenant.
