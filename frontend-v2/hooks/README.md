# `hooks/` - Logic trạng thái và hành vi tái sử dụng

## Mục tiêu
- Đóng gói logic React dùng chung dưới dạng custom hooks.
- Tách hành vi khỏi component để UI ngắn gọn và dễ bảo trì.
- Là nơi chuẩn cho state có vòng đời theo màn hình hoặc theo luồng người dùng.

## Nhiệm vụ chính
- `useCountUp.ts`: hiệu ứng số tăng dần cho hiển thị.
- `useMiniappTerms.ts`: quản lý luồng điều khoản Mini App.

## Hành vi kiến trúc bắt buộc
- Hook chỉ làm một nhóm nhiệm vụ rõ ràng.
- Side-effect phải được kiểm soát bằng dependency rõ ràng.
- Không thao túng DOM trực tiếp nếu có thể làm qua React state.
- Hook có thể dùng `lib/` để gọi API, nhưng không phụ thuộc ngược vào component cụ thể.

## Quy tắc dễ debug
- Hook nên trả về API đơn giản: dữ liệu + trạng thái + action.
- Đặt tên biến phản ánh trạng thái thật (`isLoading`, `error`, `accepted`...).
- Khi lỗi khó tái hiện, thêm log có ngữ cảnh ngay trong hook.

## Mục tiêu đào tạo nhân viên
- Biết khi nào cần tách logic từ component sang hook.
- Biết đọc vòng đời hook để tìm nguyên nhân re-render hoặc state sai.
- Biết chuẩn hóa contract trả về của hook để tái sử dụng an toàn.
