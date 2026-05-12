# `migrations/` — Alembic (Postgres schema có version)

Quản lý **thay đổi schema** sau lần bootstrap đầu. Không thay thế hoàn toàn `init_pg_schema.py` ở startup — hai cơ chế **cùng tồn tại**; quy tắc merge xem monorepo `CLAUDE.md` (mục Migration).

## Thành phần

| File / thư mục | Việc làm |
|----------------|----------|
| **`env.py`** | Cấu hình Alembic: URL DB, metadata, chế độ offline/online. |
| **`versions/*.py`** | Mỗi file = một revision: `upgrade()` / `downgrade()`. |
| **`README.md`** (file này) | Quy trình an toàn + lệnh thường dùng. |

## Quy tắc vận hành

| Tình huống | Hành động |
|------------|-----------|
| **DB mới (rỗng)** | `alembic upgrade head`. |
| **DB đã chạy `init_pg_schema` cũ, chưa có lịch sử Alembic** | `alembic stamp head` (đánh dấu, không chạy lại DDL trùng). |
| **Đổi schema mới** | Tạo revision trong container → review → `upgrade head` trên staging trước prod. |

## Cấm (production)

- Sửa tay DB ngoài migration đã review.
- `downgrade` phá dữ liệu trên prod.
- Sửa nội dung revision **đã merge**; sai → viết revision **mới** sửa tiến.

## Lệnh (cwd: `backend_ai/backend`)

```bash
alembic history
alembic current
alembic upgrade head
alembic stamp head
```

## Đào tạo nhân viên

1. Tuần 1: `env.py` + đọc 1–2 file `versions/` mẫu.
2. Tuần 2: viết migration nhỏ trên DB local (thêm cột/index).
3. Tuần 3: staging — backup → migrate → verify → kế hoạch rollback.
4. Tuần 4: review migration prod theo checklist rủi ro.
