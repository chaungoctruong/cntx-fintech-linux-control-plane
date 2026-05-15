# MT5 Connect Flow

Luồng kết nối MT5 chuẩn:
1. User nhập broker/server/login/password trên Mini App.
2. Linux control plane lưu credential đã mã hóa và tạo login-slot reservation.
3. Control plane chọn Windows runner/slot khỏe và gửi lệnh `RESERVE_OR_LOGIN_SLOT`.
4. Windows runner mở MT5 thật bằng credential đó trên slot trống.
5. Nếu login đúng, runner trả `LOGIN_SLOT_VERIFIED` và giữ slot tối đa 5 phút.
6. Nếu user bật bot trong thời gian giữ slot, backend dùng lại đúng slot đó để `START_BOT`, không login lại.
7. Nếu user không bật bot hoặc login lỗi, backend release slot.

Khi login-slot lỗi:
- Kiểm tra đúng server MT5, login và mật khẩu.
- Kiểm tra tài khoản còn quyền trading/live hay không.
- Kiểm tra runner online, slot ready, login-slot backlog và lệnh Redis.
- Cần thêm `login_reservation_id`, `runner_id`, `slot_id`, thời điểm và ảnh/log lỗi.
