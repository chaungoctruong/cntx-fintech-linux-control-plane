# Sticky Slot

Sticky slot là cơ chế giữ account gắn với một runner/slot ổn định khi có active deployment hoặc binding hợp lệ.

Nguyên tắc:
- Một account active chỉ nên gắn vào một slot active tại một thời điểm nếu chưa có thiết kế multi-deployment rõ ràng.
- Khi slot broken/degraded/stale, cần reconcile theo control plane, không đoán từ UI.
- Khi user hỏi slot lỗi gì, cần đọc runner_id, slot_id, slot_status, runner heartbeat và last error.

Không khuyên user tự clear binding hoặc restart runtime khi chưa có approve vận hành.
