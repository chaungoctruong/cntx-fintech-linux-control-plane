# Catalog SQL

## Nhiệm vụ
- Chứa SQL quản lý bot catalog: upsert, list, retire, tìm theo identity.
- Bảo đảm catalog runner và non-runner được lọc đúng.

## Lưu ý an toàn
- Các query retire phải giữ điều kiện loại trừ `runner://` đúng nghĩa.
- Những query động (NOT IN dynamic) cần giữ behavior hiện tại nếu chưa có test đầy đủ.
