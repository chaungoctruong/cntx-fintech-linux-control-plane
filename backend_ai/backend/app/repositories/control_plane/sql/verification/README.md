# `verification/` — SQL đọc job xác minh account

Truy vấn **đọc** job verification đang active / theo user / theo id. (Luồng tạo job & cập nhật kết quả có thể nằm thêm ở mixin/sql khác hoặc inline — khi sửa hãy `rg verification` trong `mixins/`.)

## File `.sql` (inventory)

- `get_active_account_verification_job.sql`
- `get_account_verification_job_for_user.sql`
- `get_account_verification_job_by_id.sql`

## Gắn với Python

- **`app/repositories/control_plane/mixins/`** + `account_verification_manager` — tìm `load_sql("verification/`.

## Luồng vận hành (khái niệm)

1. Backend tạo job (ngoài 3 file read-only này có thể có SQL/migration khác).
2. Runner **dequeue** job từ Redis queue `mt5:runner:{RUNNER_ID}:verification` (BRPOPLPUSH sang `:processing`).
3. Runner gọi `POST /api/v2/runner/account-verifications/result`.

## Lưu ý an toàn

- Trạng thái job là hợp đồng worker ↔ API — không đổi enum tùy tiện.
- Query cập nhật job cần lock semantics nếu có concurrent worker (xem code gọi).
