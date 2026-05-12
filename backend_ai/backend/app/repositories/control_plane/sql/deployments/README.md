# `deployments/` — SQL vòng đời deployment

List deployment theo user, **ownership**, đếm active, blocker runtime start, event `ORDER_FILLED` theo deployment.

## File `.sql` (inventory)

- `list_deployments.sql` — danh sách deployment (filter user).
- `select_deployment_owned_by_user.sql` — kiểm tra quyền sở hữu.
- `get_active_deployment_for_account.sql` — deployment đang active cho account.
- `count_user_active_deployments_live_only.sql` / `count_user_active_deployments_all_modes.sql` — đếm theo policy.
- `get_account_runtime_start_blocker_active.sql` / `get_account_runtime_start_blocker_snapshot.sql` — lý do chặn start.
- `list_deployment_order_filled_events.sql` / `list_deployment_order_filled_events_since.sql` — event fill lệnh.

## Gắn với Python

- **`app/repositories/control_plane/mixins/deployments.py`** (và các service gọi mixin).

## Lưu ý an toàn

- Filter `user_id` / ownership là **hàng rào bảo mật** — không được bỏ khi copy-paste.
- `LIMIT` / `ORDER BY` rõ ràng để kết quả deterministic.
