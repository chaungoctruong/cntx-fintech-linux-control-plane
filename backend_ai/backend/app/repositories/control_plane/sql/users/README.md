# Users SQL

## Nhiệm vụ
- Chứa SQL đọc/cập nhật metadata user.
- Metadata được merge theo JSONB phục vụ feature flags và profile runtime.

## Lưu ý an toàn
- Cập nhật metadata cần giữ cơ chế merge, tránh overwrite không chủ đích.
- Query users phải giữ filter theo `id` rõ ràng.
