# `runners_slots/` — SQL runner node + slot + binding

Đăng ký **runner** (`runner_nodes`), **slot** (`runner_slots`), **binding** account↔slot, heartbeat, maintenance/drain, orphaned handoff, health list.

## Nhóm file (32 `.sql`) — đọc theo prefix

| Prefix | Ý nghĩa |
|--------|---------|
| **`upsert_runner_node_on_register.sql`** | Insert/update node khi register. |
| **`update_runner_node_heartbeat.sql`** | Cập nhật heartbeat node + metadata. |
| **`insert_runner_slot_on_register.sql`** / `update_runner_slot_*` | Slot khi register / heartbeat / projection / inventory. |
| **`select_*` / `get_*` / `list_*`** | Đọc metadata, health, danh sách runner/slot. |
| **`reserve_slot_binding_*`**, **`insert_account_slot_binding`**, **`release_*`**, **`get_current_*`**, **`select_latest_*`** | Binding account ↔ runner/slot (sticky, reactivate). |
| **`set_runner_maintenance_*`**, **`prepare_orphaned_handoff_*`** | Drain / maintenance / handoff khi slot mồ côi. |
| **`count_runner_slots_by_status_for_heartbeat.sql`** | Thống kê slot theo status cho heartbeat inventory. |

*(Danh sách đầy đủ: `ls app/repositories/control_plane/sql/runners_slots/`.)*

## Gắn với Python

- **`app/repositories/control_plane/mixins/runners_slots.py`** (chính) — mọi `load_sql("runners_slots/...")`.

## Lưu ý an toàn

- Transaction **SELECT … FOR UPDATE** trong handoff: giữ thứ tự lock để tránh deadlock.
- Thay đổi projection metadata: phải idempotent + có điều kiện stale rõ (xem code `_slot_registration_should_update_projection`).

## Transport (đào tạo)

- **Lệnh bot** không nằm trong SQL này; publish Redis ở `events/` + `infra/redis_streams.py`. Thư mục này mô tả **tồn tại runner/slot** trên control-plane.
