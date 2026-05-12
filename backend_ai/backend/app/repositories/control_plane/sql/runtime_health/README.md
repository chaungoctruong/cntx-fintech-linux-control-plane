# `runtime_health/` — SQL đọc “sức khỏe” runtime

Truy vấn **read-only** (trừ khi caller bọc transaction khác) theo ngưỡng `stale_sec` từ Python: accounts, deployments, runners, slots, events.

## File `.sql` (inventory)

- `get_runtime_health_accounts.sql`
- `get_runtime_health_deployments.sql`
- `get_runtime_health_runners.sql`
- `get_runtime_health_slots.sql`
- `get_runtime_health_events.sql`

## Gắn với Python

- **`app/repositories/control_plane/mixins/`** + `api/v2/system` health — tìm `load_sql("runtime_health/`.

## Lưu ý an toàn

- Param ngưỡng stale luôn bind `%s` — không embed số giây cứng trong SQL nếu cần đổi theo env.
- Không nhét logic **ghi** nặng vào đây; ghi nằm ở `reconcile/` hoặc service.
