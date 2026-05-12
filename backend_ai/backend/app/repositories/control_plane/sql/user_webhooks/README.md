# `user_webhooks/` — SQL webhook user

CRUD webhook URL user dùng cho **delivery** worker (HTTP callback ra ngoài).

## File `.sql` (inventory)

- `insert_user_webhook.sql` / `delete_user_webhook.sql`
- `list_user_webhooks.sql` / `list_user_webhooks_with_secret.sql`

## Gắn với Python

- **`app/repositories/control_plane/mixins/`** + `webhook_delivery_service` — tìm `load_sql("user_webhooks/`.

## Lưu ý an toàn

- Chỉ query `with_secret` khi thật sự cần secret (rotate/debug có kiểm soát).
- Mọi thao tác phải có `user_id` để chống cross-tenant.
