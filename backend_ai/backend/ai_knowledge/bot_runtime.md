# Bot Runtime

Bot runtime chạy ở Windows runner slot, không chạy trong Linux backend.

Start bot:
1. Mini App gửi yêu cầu start.
2. Linux control plane chống double-start theo account.
3. Scheduler chọn runner/slot khỏe, ưu tiên sticky binding hợp lệ.
4. Linux tạo deployment và command.
5. Command đi vào queue theo runner.
6. Runner hydrate package qua `/api/v2/runner/deployments/{deployment_id}/package`.
7. Runner chạy bot và gửi event/heartbeat/log về Linux.

Khi hỏi trạng thái bot:
- Không bịa RUNNING/OFF.
- Dựa vào backend context: linked accounts, running accounts, heartbeat, deployment state.
- Nếu context chưa đủ, hỏi đúng account/login, bot code hoặc thời điểm phát sinh.
