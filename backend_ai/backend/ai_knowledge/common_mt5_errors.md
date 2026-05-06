# Common MT5 Errors

Checklist khi bot không vào lệnh MT5:
- Bot/deployment có đang RUNNING đúng account và bot code không.
- Runner heartbeat có fresh không, slot có ready/broken/degraded không.
- MT5 terminal có bật AutoTrading/Algo Trading không.
- EA có AllowLiveTrading không.
- Symbol có market closed, suffix khác hoặc trade disabled không.
- Spread/slippage có giãn bất thường không.
- Free margin có đủ cho lot hiện tại không.
- Server/login/password có đúng không.
- Broker có reject order, requote, off quotes, invalid stops, invalid volume không.
- Cần log có `deployment_id`, `command_id`, `runner_id`, `slot_id`, thời điểm và mã lỗi broker/MT5.

Không kết luận chắc nguyên nhân khi chưa có log hoặc trạng thái runtime.
