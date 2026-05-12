# `lib/` - Lõi tích hợp và tiện ích nền

## Mục tiêu
- Chứa lớp truy cập API, adapter nền tảng và tiện ích dùng chung.
- Tập trung hóa logic tích hợp để route/component không gọi hạ tầng trực tiếp.
- Giữ contract dữ liệu nhất quán giữa frontend và backend.

## Nhiệm vụ chính (inventory file)

- `api.ts`: lớp gọi backend API, chuẩn hóa request/response và lỗi.
- `store.ts`: quản lý state dùng chung ở mức ứng dụng.
- `telegram.ts`: adapter tích hợp Telegram Mini App.
- `mt5-preferences.ts`: tiện ích lưu/đọc tùy chọn liên quan MT5 phía client.
- `clientLogger.ts`: gom lỗi client / beacon tới backend telemetry (đồng bộ với endpoint `/api/v2/system/client-events` khi bật).

## Hành vi kiến trúc bắt buộc
- Mọi gọi API đi qua `lib/api.ts` (hoặc wrapper từ file này).
- Chuẩn hóa xử lý lỗi tại `lib/` trước khi đẩy lên UI.
- Không để component tự ghép URL/headers tùy tiện.
- Hạn chế side-effect toàn cục ngoài các file adapter chuyên trách.
- Endpoint cần Telegram auth chỉ được gọi khi có `Telegram.WebApp.initData`; nếu mở ngoài Telegram thì báo người dùng mở trong Mini App, không spam backend bằng request 401.

## Quy tắc dễ debug
- Lỗi mạng: kiểm tra tại `lib/api.ts` trước, rồi mới kiểm tra component.
- Thêm context vào thông báo lỗi (endpoint, trạng thái, mã nghiệp vụ).
- Phân biệt rõ lỗi backend, lỗi mạng, lỗi dữ liệu đầu vào.

## Mục tiêu đào tạo nhân viên
- Biết đường đi của dữ liệu từ UI -> hook -> lib -> backend.
- Biết thêm endpoint mới đúng vị trí mà không phá cấu trúc.
- Biết chuẩn hóa kiểu dữ liệu trả về để giảm bug runtime.
