# Linux Backend

Linux backend là control plane, không phải execution plane.

Trách nhiệm:
- Quản lý account, deployment, scheduler, queue, heartbeat, event và health reconcile.
- Lưu trạng thái vào PostgreSQL.
- Không tự trade và không place order trực tiếp vào MT5.
- Không chứa bot runtime state chi tiết.

Khi AI trả lời user:
- Với trạng thái bot/account, phải đọc backend context.
- Nếu chưa có context, nói rõ thiếu user/account/deployment nào.
- Không bịa RUNNING/OFF chỉ từ câu hỏi.
