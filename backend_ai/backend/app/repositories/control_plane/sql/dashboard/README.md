# Dashboard SQL

## Nhiệm vụ
- Chứa các truy vấn tổng hợp nhẹ cho dashboard (accounts, deployments, pnl).
- Tối ưu để lấy nhanh snapshot theo user.

## Lưu ý an toàn
- Giữ output schema ổn định để API/FE không bị vỡ.
- Tránh đưa logic xử lý phức tạp vào đây; chỉ tổng hợp dữ liệu.
