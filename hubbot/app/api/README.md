# API Layer

## Nhiệm vụ
- Đóng gói toàn bộ giao tiếp giữa hubbot và backend API.
- Cung cấp retry, cache ngắn hạn, singleflight, và dedup để giảm tải.

## Quy tắc
- Không gọi `httpx` trực tiếp ở command/callback/message; dùng qua `api_json`.
- Mọi lỗi mạng/5xx cần có log rõ và phát cảnh báo vận hành phù hợp.
- Giữ payload nhất quán, không biến đổi kiểu dữ liệu ngoài dự kiến backend.

## Mục tiêu đào tạo
- Nhân viên mới hiểu nơi đặt logic tích hợp backend.
- Biết cách thêm endpoint mới mà không phá chuẩn timeout/retry/dedup hiện có.
