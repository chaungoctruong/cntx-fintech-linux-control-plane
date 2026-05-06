# Verification SQL

## Nhiệm vụ
- Chứa SQL cho vòng đời xác minh account: tạo job, claim, cập nhật kết quả, đọc trạng thái.
- Hỗ trợ tracking SLA và projection trạng thái verification.

## Lưu ý an toàn
- Trạng thái job (`pending/dispatched/...`) là hợp đồng giữa worker và API, không đổi tùy tiện.
- Các query claim/update cần giữ lock semantics để tránh double-processing.
