# `catalog/` — SQL bot catalog (MT5 bots)

Upsert / list / retire bản ghi **catalog bot** (metadata bot trading, version, checksum).

## File `.sql` (inventory)

- `list_bots.sql` — liệt kê catalog (filter runner vs non-runner theo query).
- `get_bot_by_name.sql` — lấy bot theo tên/code.
- `upsert_bot_catalog_entry.sql` / `upsert_bot_version.sql` — ghi catalog + version.
- `retire_bot_catalog_entries.sql` — retire có kiểm soát.
- `retire_stale_runner_bot_catalog_when_active.sql` / `retire_stale_runner_bot_catalog_no_active.sql` — dọn catalog runner cũ theo điều kiện active.
- `retire_missing_bots_all_non_runner.sql` — dọn bot non-runner thiếu trên disk/sync.

## Gắn với Python

- **`app/repositories/control_plane/mixins/`** (catalog / bot catalog service) — tìm `load_sql("catalog/`.

## Lưu ý an toàn

- Query retire phải giữ điều kiện loại trừ prefix `runner://` đúng nghĩa.
- Thay đổi `NOT IN` động cần regression vì ảnh hưởng runner sync.
