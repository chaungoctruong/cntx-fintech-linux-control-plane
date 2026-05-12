# `runner_bot_state/` — SQL state GSAlgo / bot trên runner

Ghi nhận **state record** từ runner (GsAlgo bridge): upsert, đóng pending, aggregate PnL realized.

## File `.sql` (inventory)

- `upsert_runner_bot_state_record.sql`
- `runner_bot_state_record_exists.sql` / `load_active_runner_bot_state_pending_entry.sql` / `close_runner_bot_state_pending_entry.sql`
- `sum_runner_bot_state_realized_pnl.sql` / `select_runner_bot_state_realized_pnl_recent.sql`

## Gắn với Python

- **`app/services/runner_gsalgo_state.py`** + route runner bot-state — tìm `load_sql("runner_bot_state/`.

## Lưu ý an toàn

- Khóa idempotency (`bot_id` / `account_id` / `deployment_id` / `record_key`) phải giữ contract với runner.
- Đổi nghĩa status record cần migration + đồng bộ repo Windows nếu có field chung.
