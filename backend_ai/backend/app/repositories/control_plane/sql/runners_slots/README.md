# Runners Slots SQL

## Nhiệm vụ
- Chứa SQL quản lý runner nodes và slot inventory: register, heartbeat, maintenance, binding.
- Hỗ trợ handoff/orphan recovery và sức khỏe runner-slot.

## Lưu ý an toàn
- Các query lock/update slot cần giữ logic tránh race condition.
- Nếu có query projection theo heartbeat, cần giữ idempotent và có điều kiện stale rõ ràng.
