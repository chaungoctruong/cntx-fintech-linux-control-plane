# `users/` — SQL metadata user

Đọc/ghi **`metadata_json`** user (feature flags, profile phụ) — merge JSONB, không thay thế bảng auth chính.

## File `.sql` (inventory)

- `get_user_metadata.sql`
- `update_user_metadata.sql`

## Gắn với Python

- Mixin users / service user — tìm `load_sql("users/`.

## Lưu ý an toàn

- Update phải **merge** JSONB đúng semantics hiện tại — tránh overwrite toàn bộ nhánh khác đang dùng.
- Query theo `id` user rõ ràng.
