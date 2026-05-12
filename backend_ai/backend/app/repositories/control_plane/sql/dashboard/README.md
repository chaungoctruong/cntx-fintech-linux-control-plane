# `dashboard/` — SQL snapshot dashboard user

Truy vấn **tổng hợp nhẹ** cho Mini App dashboard: account, deployment, PnL summary — tránh logic nặng trong API.

## File `.sql` (inventory)

- `get_dashboard_account_summary.sql`
- `get_dashboard_deployment_summary.sql`
- `get_dashboard_pnl_summary.sql`

## Gắn với Python

- Mixin dashboard / miniapp service — tìm `load_sql("dashboard/`.

## Lưu ý an toàn

- Giữ **tên cột output** ổn định (FE parse theo field).
- Chỉ aggregate; không nhét business rule phức tạp vào SQL này.
