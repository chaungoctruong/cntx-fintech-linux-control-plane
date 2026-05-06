# Reconcile SQL

## Nhiệm vụ
- Chứa SQL đối soát state runtime: stale heartbeat, stale deployment, stale snapshot, projection refresh.
- Đồng bộ lại trạng thái deployment/slot/binding/command sau sự cố.

## Lưu ý an toàn
- Đây là nhóm query có tác động ghi lớn; cần ưu tiên idempotent và điều kiện rõ ràng.
- Mọi thay đổi phải giữ nguyên thứ tự params và tuple status để tránh sai behavior production.
