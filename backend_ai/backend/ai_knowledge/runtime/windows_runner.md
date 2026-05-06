# Windows Runner

Windows runner là execution plane của CNTx labs.

Trách nhiệm:
- Chạy MT5 terminal và runtime bot trong slot.
- Giữ state runtime chi tiết của bot.
- Nhận command từ queue theo runner.
- Hydrate deployment package từ Linux control plane.
- Gửi heartbeat, execution event và runtime log về Linux.

Khi debug:
- Xem runner online/stale trước.
- Xem slot ready/allocated/degraded/broken.
- Xem deployment heartbeat và command delivery lifecycle.
- Không kết luận bot lỗi chiến lược khi runner/slot đang stale hoặc broken.
