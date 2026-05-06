# Deployment Lifecycle

Deployment lifecycle chuẩn:
1. draft hoặc selected.
2. start_requested.
3. starting.
4. running.
5. stop_requested.
6. stopped hoặc failed.

Command delivery lifecycle:
- queued
- dispatched
- acknowledged hoặc failed

Khi user hỏi bot đang chạy không:
- Đọc deployment status, desired_state, health_status và last_heartbeat_at.
- Đọc command gần nhất nếu deployment đang kẹt start/stop.
- Đọc runner/slot status nếu deployment có runner_id/slot_id.
