# `snapshots/` — SQL snapshot account / position

Lưu và đọc **snapshot** trạng thái account và position theo thời gian — audit, dashboard, so sánh lịch sử.

## File `.sql` (inventory)

- `upsert_account_state_snapshot.sql` / `get_account_state.sql`
- `upsert_position_snapshot.sql`
- `list_position_snapshots.sql` / `list_position_snapshots_base.sql` / `list_position_snapshots_by_deployment.sql`

## Gắn với Python

- Mixins snapshots / deployment health — tìm `load_sql("snapshots/`.

## Lưu ý an toàn

- Sort theo `snapshot_at`, `id` phải ổn định giữa các biến thể list.
- Hai nhánh list (by deployment / base) cần **cùng schema cột** nếu API merge kết quả.
