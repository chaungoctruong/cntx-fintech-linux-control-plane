# Runner Bot State SQL

## Nhiệm vụ
- Chứa SQL ghi nhận và truy vấn state records của bot trên runner.
- Hỗ trợ idempotency check, close pending record, tổng hợp realized PnL.

## Lưu ý an toàn
- Khóa danh tính record (`bot_id/account_id/deployment_id/record_key`) phải giữ nguyên.
- Tránh thay đổi nghĩa status record nếu chưa có migration/test kèm theo.
