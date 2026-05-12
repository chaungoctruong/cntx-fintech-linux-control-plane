# `commands/` — SQL hàng đợi lệnh runner (`execution_commands`)

Tạo và theo dõi **`execution_commands`**: pending/queued/dispatched/acknowledged/failed, replay Redis, audit, runtime log, reconcile terminal bot control.

## File `.sql` (inventory)

**Tạo / đọc / cập nhật lệnh**

- `create_execution_command.sql`
- `get_execution_command.sql` / `get_execution_command_by_trace_identity.sql`
- `get_pending_account_start_stop_command.sql` / `get_recent_bot_control_command_for_user.sql`
- `update_execution_command_delivery.sql` / `mark_command_delivery.sql` / `mark_command_processing_requeued.sql` / `mark_command_replay_failure.sql`
- `fail_pending_start_commands_for_deployment.sql`

**List / backlog / stale**

- `list_execution_commands.sql` / `list_execution_events.sql` / `list_execution_audit.sql` / `list_runtime_logs.sql`
- `list_replayable_execution_commands_base.sql` / `count_command_delivery_replay_backlog_base.sql`
- `list_stale_processing_execution_commands.sql` / `list_stale_queued_start_commands.sql`

**Insert phụ trợ**

- `insert_execution_event.sql` / `insert_runtime_log.sql` / `upsert_execution_audit.sql`

**Reconcile terminal**

- `reconcile_terminal_bot_control_commands.sql`

## Gắn với Python

- **`app/repositories/control_plane/mixins/commands.py`** — `CommandRouterService`, reconciler, ingest.

## Transport (quan trọng khi đào tạo)

- Postgres là **source of truth** cho trạng thái lệnh.
- Runner lấy payload thực thi từ **Redis list** `mt5:runner:{runner_id}:commands` (publish từ `infra/redis_streams.py`). **Không** có HTTP poll lệnh từ backend hiện tại.

## Lưu ý an toàn

- Không đổi tuple `delivery_status` tùy tiện — mọi worker/reconciler phụ thuộc.
- Luôn bind param; SQL có thể được bọc thêm filter ở Python — giữ thứ tự `execute(params)`.
