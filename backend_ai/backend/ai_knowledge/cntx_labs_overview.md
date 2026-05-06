# CNTx labs Overview

CNTx labs là nền tảng SaaS điều phối bot trading cho người dùng qua Telegram Mini App.

Boundary vận hành hiện tại:
- Linux backend là control plane: nhận request, lưu cấu hình, chọn Windows runner/slot, phát command, nhận event/heartbeat và trả trạng thái cho Mini App.
- Windows runner là execution plane: chạy runtime bot trong slot, giữ state runtime và kết nối MT5.
- Bot logic không nằm trong Linux backend.
- Khi hỏi trạng thái bot/account, không được đoán. Phải dựa vào backend runtime context hoặc yêu cầu account/login/thời điểm để kiểm tra tiếp.

Flow người dùng:
1. Mở bot Telegram và gõ `/start`.
2. Kết nối tài khoản giao dịch.
3. Chọn bot.
4. Bật/tắt bot trong Quản lý Bot.
5. Theo dõi trạng thái, PnL, log và cảnh báo.
