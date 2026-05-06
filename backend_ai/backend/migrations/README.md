# Migrations - Hướng Dẫn Nhiệm Vụ và Vận Hành

## Mục tiêu thư mục
- Quản lý migration schema PostgreSQL theo version bằng Alembic.
- Đảm bảo mọi thay đổi cấu trúc DB có lịch sử rõ ràng, có thể audit và triển khai an toàn.
- Là cầu nối chuyển đổi từ bootstrap DDL (`init_pg_schema.py`) sang vận hành migration đầy đủ.

## Nhiệm vụ của từng phần
- `env.py`:
  - Cấu hình runtime cho Alembic (kết nối DB, metadata context, offline/online mode).
  - Là điểm vào kỹ thuật cho lệnh `alembic`.
- `versions/`:
  - Chứa từng revision migration theo thứ tự thời gian.
  - Mỗi file mô tả một thay đổi schema cụ thể (bảng/cột/index/ràng buộc).
- `README.md` (file này):
  - Mô tả quy trình làm việc an toàn, hành vi bắt buộc và checklist triển khai.

## Logic vận hành chuẩn
- Với DB production/staging đã tồn tại:
  1. Xác minh schema hiện tại bằng `init_pg_schema.py`.
  2. Đồng bộ state revision bằng `alembic stamp head`.
  3. Từ thời điểm đó, mọi thay đổi schema mới đi qua `versions/*`.
- Với DB mới/tạm (staging scratch):
  - Có thể dùng `alembic upgrade head` để dựng schema theo chuỗi revision.

## Hành vi bắt buộc (an toàn production)
- Không sửa trực tiếp DB production ngoài migration đã được review.
- Không chạy downgrade phá hủy trên production.
- Không chỉnh sửa nội dung revision đã phát hành; nếu sai thì tạo revision sửa tiến (forward fix).
- Không bỏ qua bước backup/PITR trước migration có rủi ro.
- Không merge migration mới nếu chưa kiểm tra thứ tự dependency và khả năng tương thích.

## Quy tắc viết migration
- Mỗi migration chỉ nên có một mục tiêu thay đổi rõ ràng.
- Đặt tên file revision dễ hiểu theo mốc thời gian + ý nghĩa nghiệp vụ.
- Với thay đổi dữ liệu lớn:
  - Ưu tiên chia nhỏ theo batch.
  - Tránh lock bảng lâu.
  - Cân nhắc tạo index đồng thời theo chiến lược phù hợp môi trường.
- Luôn bổ sung phần `downgrade()` hợp lý cho môi trường non-production.

## Lệnh nền tảng (chạy tại `backend_ai/backend`)
```bash
alembic history
alembic current
alembic stamp head
```

## Mục tiêu đào tạo nhân viên mới
- Tuần 1: hiểu vòng đời migration và vai trò `env.py` + `versions/`.
- Tuần 2: đọc/viết migration đơn giản (thêm cột/index) trên DB local.
- Tuần 3: thực hành quy trình staging: backup -> migrate -> verify -> rollback plan.
- Tuần 4: tham gia review migration production với checklist rủi ro.
