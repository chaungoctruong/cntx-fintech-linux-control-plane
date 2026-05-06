# Commands SQL

## Nhiệm vụ
- Chứa SQL cho command queue: tạo, claim, cập nhật trạng thái delivery, replay, audit.
- Hỗ trợ event log/runtime log và các truy vấn quan sát backlog.

## Lưu ý an toàn
- Trạng thái command (`pending/queued/dispatched/...`) là nguồn sự thật cho worker, không đổi tuple trạng thái tùy tiện.
- Các truy vấn base có thể được nối thêm filter ở Python, cần giữ thứ tự params.
