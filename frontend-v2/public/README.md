# `public/` - Tài nguyên tĩnh của web

## Mục tiêu
- Chứa tài nguyên tĩnh phục vụ trực tiếp qua URL public.
- Cung cấp logo, font, icon và asset không cần bundle động.
- Giữ cấu trúc tài nguyên ổn định để tránh lỗi đường dẫn ở production.

## Nhiệm vụ chính
- `cntx-labs-logo.svg`: logo dùng chung giao diện.
- `fonts/`: font tĩnh phục vụ rendering nhất quán.
- `.gitkeep`: giữ cấu trúc thư mục khi chưa có đủ asset.

## Hành vi kiến trúc bắt buộc
- Asset tĩnh cần đặt tên rõ nghĩa, tránh trùng và khó truy vết.
- Không để file tạm hoặc file build rác trong `public/`.
- Với asset lớn, cân nhắc tối ưu kích thước trước khi commit.

## Quy tắc dễ debug
- Lỗi 404 asset: kiểm tra đúng path bắt đầu từ `/` theo cấu trúc `public/`.
- Lỗi font: kiểm tra đường dẫn file và khai báo trong CSS.
- Nếu cache trình duyệt gây lệch, dùng cơ chế bust cache theo phiên bản.

## Mục tiêu đào tạo nhân viên
- Biết phân biệt khi nào để file trong `public/` và khi nào import qua code.
- Biết tổ chức asset theo nhóm (logo/font/icon) để team dễ tìm.
- Biết kiểm tra nhanh lỗi asset trong môi trường staging/production.
