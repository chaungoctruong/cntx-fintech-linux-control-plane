# `app/services/broker/` — Lane cTrader (legacy / read-only theo định hướng)

Adapter HTTP cho **cTrader public beta** — giữ tương thích UI/Mini App. **Không** là lane thực thi chính của MT5.

## File trong thư mục

| File | Việc làm |
|------|----------|
| **`ctrader_api_client.py`** | Client async gọi broker API cTrader: timeout, header, mapping lỗi. |
| **`ctrader_public_beta.py`** | Tổng hợp trạng thái lane (online/degraded/offline) cho API. |
| **`__init__.py`** | Export package. |
| **`LEGACY_READONLY.md`** | Phạm vi freeze / không mở rộng tùy tiện — đọc trước khi sửa. |
| **`README.md`** (file này) | Hướng dẫn đào tạo + ranh giới. |

## Ranh giới

- Lane chính sản phẩm: **MT5** qua Windows runner + control-plane Redis/HTTP trong `app/services/control_plane_service.py` / `events/`.
- Không nhét logic đặt lệnh MT5 đầy đủ vào đây.
- Không mở broker adapter **mới** trong thư mục này nếu chưa có quyết định kiến trúc.

## Khi chỉnh sửa

- Giữ contract response để FE không vỡ.
- Degrade an toàn (payload rỗng có ý nghĩa) thay vì 500 không kiểm soát.

## Đào tạo nhân viên

1. Đọc `LEGACY_READONLY.md`.
2. Trace một request Mini App liên quan cTrader → `ctrader_public_beta` → client.
3. So sánh với luồng MT5 trong `api/v2/accounts.py` / deployments (lane chính).
