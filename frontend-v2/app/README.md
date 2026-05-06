# `app/` - Cấu trúc route và khung giao diện

## Mục tiêu
- Chứa toàn bộ route theo App Router của Next.js.
- Định nghĩa layout gốc, trang chủ và các nhánh trang theo sản phẩm.
- Tách rõ phần điều hướng UI khỏi logic gọi API/điều phối dữ liệu.

## Nhiệm vụ chính
- `layout.tsx`: khung gốc, metadata, providers mức ứng dụng.
- `page.tsx`: trang chính của frontend.
- `bot/`, `wallet/`, `rewards/`, `rankbot/`: các phân vùng route theo tính năng.
- `globals.css`: style nền tảng toàn app.

## Hành vi kiến trúc bắt buộc
- Route chỉ nên điều phối hiển thị và ghép component.
- Logic nghiệp vụ phức tạp chuyển xuống `lib/` hoặc `hooks/`.
- Tránh gọi API trực tiếp rải rác trong quá nhiều route file.
- Giữ cấu trúc route nhất quán để dễ debug theo URL.

## Quy tắc dễ debug
- Mỗi route có một điểm vào rõ ràng (file `page.tsx`).
- Khi lỗi UI, kiểm tra theo thứ tự: route -> component -> hook -> lib API.
- Không trộn state toàn cục vào `layout` nếu state chỉ thuộc một màn hình.

## Mục tiêu đào tạo nhân viên
- Hiểu map URL sang thư mục trong `app/`.
- Biết phân biệt phần “điều hướng trang” và phần “xử lý dữ liệu”.
- Biết lần theo lỗi render từ route file ra component/hook liên quan.
