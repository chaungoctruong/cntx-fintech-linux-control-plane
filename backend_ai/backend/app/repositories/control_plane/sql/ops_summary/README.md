# `ops_summary/` — SQL snapshot vận hành

Một lần query gom **số liệu** runners, slots, verification, commands, deployments, events, binding — phục vụ endpoint ops/monitor.

## File `.sql` (inventory)

- `get_ops_summary_runners.sql`
- `get_ops_summary_slots.sql`
- `get_ops_summary_verification.sql`
- `get_ops_summary_commands.sql`
- `get_ops_summary_deployments.sql`
- `get_ops_summary_events.sql`
- `get_ops_summary_bindings_sticky_mismatch.sql`
- `list_runner_ids_ordered.sql`

## Gắn với Python

- **`app/repositories/control_plane/mixins/`** + API `system` / admin ops — tìm `load_sql("ops_summary/`.

## Lưu ý an toàn

- Giữ **tên field** output ổn định (dashboard/alert parse theo key).
- Ngưỡng `stale` truyền từ Python (`%s`); không hard-code vào SQL nếu cần đổi theo môi trường.
