# Runtime Common Errors

Các nhóm lỗi thường gặp:
- Account credential: invalid account, authorization failed, wrong password, invalid server.
- Runner/slot: runner offline, runner stale, slot broken, slot degraded, slot busy.
- MT5 permission: AutoTrading/Algo Trading off, AllowLiveTrading off, account trade disabled.
- Market/order: market closed, invalid volume, invalid stops, not enough money, off quotes, requote, spread giãn.
- Queue/lifecycle: command queued lâu, dispatched chưa acknowledged, failed gần đây.

Khi chưa có log:
- Trả checklist ngắn.
- Hỏi đúng `deployment_id`, `command_id`, `runner_id`, `slot_id`, thời điểm và retcode/order_send nếu có.
