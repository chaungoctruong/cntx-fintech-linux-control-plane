# Windows Runner

Windows runner là execution plane của CNTx labs.

Trách nhiệm:
- Chạy MT5 terminal và runtime bot trong slot.
- Giữ state runtime chi tiết của bot.
- Nhận **lệnh điều khiển bot** từ **Redis** (`mt5:runner:{RUNNER_ID}:commands`, `RUNNER_TRANSPORT=redis_queue`) — không lấy lệnh qua HTTP long-poll từ control-plane.
- Hydrate deployment package từ Linux control-plane (HTTP ngắn: bootstrap, package, health check).
- Gửi heartbeat, execution event và runtime log về Linux (**HTTP** `/api/v2/runner/*`).

Khi debug:
- Xem runner online/stale trước.
- Xem slot ready/allocated/degraded/broken.
- Xem deployment heartbeat và command delivery lifecycle.
- Không kết luận bot lỗi chiến lược khi runner/slot đang stale hoặc broken.
