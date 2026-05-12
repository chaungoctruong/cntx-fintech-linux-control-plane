# `reconcile/` — SQL đối soát / sửa trạng thái hàng loạt

Cập nhật Postgres khi **stale**: deployment `stop_requested`, snapshot account, slot projection, runner offline, bootstrap failure, v.v. Nhóm **ghi nhiều** — chỉ chạy qua code reconciler đã review.

## File `.sql` (inventory)

- `reconcile_mark_deployments_health_stale.sql`
- `reconcile_mark_account_state_snapshots_stale.sql`
- `reconcile_mark_runner_nodes_offline.sql`
- `reconcile_stale_stop_requested_deployments.sql`
- `reconcile_start_bootstrap_failures.sql`
- `reconcile_slot_projection_from_events.sql`
- `reconcile_refresh_sticky_slot_projection.sql`
- `reconcile_refresh_runner_slot_counts_metadata.sql`

## Gắn với Python

- **`app/monitoring/control_plane_reconciler.py`** và/hoặc service reconciler — tìm `load_sql("reconcile/`.

## Lưu ý an toàn

- Phải **idempotent** và điều kiện `WHERE` chặt — tránh quét cả bảng không chủ đích.
- Đổi thứ tự params hoặc tuple status → regression nghiêm trọng; cần test + migration nếu đổi schema.
