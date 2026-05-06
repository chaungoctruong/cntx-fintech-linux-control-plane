# cTrader Backend Lane (Read-Only theo định hướng hiện tại)

## Mục tiêu thư mục
- Chứa adapter backend cho lane cTrader public beta.
- Cung cấp lớp gọi API broker backend và lớp tổng hợp trạng thái public beta cho API.
- Duy trì tương thích cho UI cTrader đang còn hiển thị trong Mini App.

## Thành phần chính
- `ctrader_api_client.py`:
  - Client HTTP bất đồng bộ gọi downstream cTrader broker API.
  - Chuẩn hóa timeout, API key, và mapping lỗi backend.
- `ctrader_public_beta.py`:
  - Tổng hợp trạng thái lane cTrader (online/degraded/offline).
  - Trả payload mô tả khả năng hiện có theo ngữ cảnh "public beta thủ công".
- `__init__.py`:
  - Điểm export package.
- `LEGACY_READONLY.md`:
  - Tài liệu freeze phạm vi cTrader trong control plane.

## Logic vận hành
- Lane cTrader hiện chỉ phục vụ:
  - Connect account.
  - Đồng bộ account.
  - Chọn account mặc định.
  - Start/stop/evaluate deployment ở mức public beta thủ công.
  - Theo dõi trạng thái runtime tổng hợp.
- Không phải execution lane chính của sản phẩm.
- Execution lane chính hiện tại là MT5 trên Windows runner.

## Hành vi bắt buộc
- Không mở rộng thêm broker adapter mới trong thư mục này.
- Không đưa logic runtime execution đầy đủ vào lane cTrader.
- Không dùng lane này để thay thế control-plane MT5 hiện hành.
- Không import chéo từ lane ngoài phạm vi đã định.

## Quy tắc an toàn khi chỉnh sửa
- Giữ nguyên contract response để API/FE không bị vỡ.
- Mọi thay đổi mapping lỗi phải giữ tính rõ ràng và không nuốt lỗi quan trọng.
- Ưu tiên degrade an toàn (fallback payload) thay vì ném exception gây sập flow.

## Mục tiêu đào tạo nhân viên
- Hiểu đây là lane "duy trì tương thích", không phải hướng mở rộng chính.
- Nắm ranh giới giữa cTrader public beta và MT5 runtime chính thức.
- Biết cách điều tra lỗi theo tầng: API -> cTrader client -> downstream response.
