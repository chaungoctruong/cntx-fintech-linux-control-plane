# `billing/` — SQL subscription user

Truy vấn **gói subscription** (billing) của user — phục vực entitlement / hiển thị.

## File `.sql` (inventory)

- `get_user_active_subscription.sql` — subscription active mới nhất của user.

## Gắn với Python

- **`app/repositories/control_plane/repository.py`** hoặc mixin billing (tìm `load_sql("billing/`).

## Lưu ý an toàn

- `ORDER BY updated_at DESC, id DESC` (hoặc tương đưong) để kết quả ổn định.
- Không đổi nghĩa “active” nếu chưa có migration + review product.
