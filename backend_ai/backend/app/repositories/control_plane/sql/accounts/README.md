# `accounts/` — SQL broker account & risk

Truy vấn Postgres cho **tài khoản MT5/broker** gắn user: list, risk policy, PnL ngày, soft-delete, scrub credential.

## File `.sql` (inventory)

- `get_account.sql` — đọc một account theo id (kèm ràng buộc user khi gọi từ API).
- `list_accounts_for_user.sql` — danh sách account của user.
- `find_mt5_account_identity_conflict.sql` — phát hiện trùng identity (login/server) khi connect.
- `get_account_risk_policy.sql` / `update_account_risk_policy.sql` — đọc/ghi policy rủi ro.
- `count_broker_accounts_for_user.sql` / `count_user_accounts_with_risk_policy.sql` — đếm phục vụ quota/guard.
- `list_accounts_with_active_circuit_breaker.sql` — account đang breaker.
- `compute_realized_pnl_today_for_account.sql` — PnL realized trong ngày (aggregate).
- `scrub_account_credentials_for_user.sql` — xóa/ghi đè secret khi user revoke.
- `soft_delete_broker_accounts_by_user.sql` — soft-delete theo user.

## Gắn với Python

- **`app/repositories/control_plane/mixins/accounts.py`** (+ một phần query trong `repository.py` cho risk/count).

## Lưu ý an toàn

- Giữ thứ tự `%s` khớp caller; không nối chuỗi SQL từ input user.
- Mọi query theo user phải giữ `user_id` / ownership đúng contract API.
