# Snapshots SQL

## Nhiệm vụ
- Chứa SQL lưu/đọc account snapshot và position snapshot.
- Phục vụ truy vết trạng thái và hiển thị lịch sử theo account/deployment.

## Lưu ý an toàn
- Thứ tự sort snapshot (`snapshot_at`, `id`) phải ổn định.
- Query list theo deployment và không theo deployment cần giữ output schema giống nhau.
