# `components/` - Thư viện UI tái sử dụng

## Mục tiêu
- Chứa các thành phần giao diện dùng lại nhiều nơi.
- Chuẩn hóa trải nghiệm người dùng giữa các màn hình.
- Giảm trùng lặp markup/style và giúp refactor an toàn hơn.

## Nhiệm vụ chính (inventory — khớp tree hiện tại)

- **Khung trang:** `AppShell.tsx`, `PageHeader.tsx`, `BottomNav.tsx`, `PageTransition.tsx`, `FeatureButton.tsx`.
- **Chung / market:** `BalanceCard.tsx`, `CntxMarketScanner.tsx`, `Toast.tsx`, `DisclaimerAcknowledgment.tsx`.
- **`Bot/`:** `Mt5BotControlPanel.tsx`, `MiniappTermsModal.tsx`, `mt5ControlMessages.ts`, `mt5ControlUtils.ts`, hooks cục bộ `useMt5BotActions.ts`, `useMt5BotControl.ts`, `useMt5BotDerivedState.ts`.
- **`Wallet/`:** `WalletBalanceCard.tsx`, `DepositSection.tsx`, `WithdrawSection.tsx`, `TransactionHistory.tsx`.
- **`Rewards/`:** `Leaderboard.tsx`, `BonusHistory.tsx`.

## Hành vi kiến trúc bắt buộc
- Component ưu tiên nhận dữ liệu qua props rõ ràng.
- Tránh nhúng logic gọi API trực tiếp trong component trình bày.
- Tách component lớn thành khối nhỏ để dễ test và dễ đọc.
- Giữ tên component phản ánh đúng chức năng hiển thị.

## Quy tắc dễ debug
- Mỗi component giữ một trách nhiệm hiển thị chính.
- Lỗi giao diện: kiểm tra props đầu vào trước khi sửa style.
- Không xử lý side-effect nặng trong render path.

## Mục tiêu đào tạo nhân viên
- Biết chọn đúng component có sẵn trước khi viết mới.
- Biết phân loại component trình bày và component theo nghiệp vụ.
- Biết cách truy vết lỗi UI theo cây component.
