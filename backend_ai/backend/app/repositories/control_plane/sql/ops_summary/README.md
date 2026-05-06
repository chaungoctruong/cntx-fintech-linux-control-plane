# Ops Summary SQL

## Nhiệm vụ
- Chứa SQL tổng hợp chỉ số vận hành (runners, slots, verification, commands, deployments, events).
- Phục vụ endpoint snapshot cho monitor/ops.

## Lưu ý an toàn
- Trường thống kê phải giữ tên ổn định để dashboard và alert không vỡ.
- Nếu đổi ngưỡng stale, đổi ở code Python (params), không hard-code vào SQL.
