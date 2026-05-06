# MT5 Connect Flow

Luồng kết nối MT5 chuẩn:
1. User nhập broker/server/login/password trên Mini App.
2. Linux control plane lưu credential đã mã hóa và tạo verification job.
3. Control plane chọn Windows runner/slot khỏe để verify.
4. Runner lấy credential qua internal secure endpoint, không qua command queue plaintext.
5. Runner callback kết quả verify về Linux.
6. Chỉ khi account connected/verified mới dùng để start deployment.

Khi verify lỗi:
- Kiểm tra đúng server MT5, login và mật khẩu.
- Kiểm tra tài khoản còn quyền trading/live hay không.
- Kiểm tra runner online, slot ready, queue verification không backlog.
- Cần thêm `verification_job_id`, `runner_id`, `slot_id`, thời điểm và ảnh/log lỗi.
