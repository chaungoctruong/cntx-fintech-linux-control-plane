# LEGACY_READONLY — `app/services/broker/`

Gói này triển khai lane cTrader public beta và các helper API client.
Lane này đang ở trạng thái **đóng băng (freeze)** theo định hướng toàn repo
(xem `docs/linux-backend-control-plane-mt5-directive.md` mục 0).

## Trạng thái hiện tại

- Linux Control Plane tập trung hướng chính: **MT5 + Windows runner**.
- Hướng `cTrader` đang **tạm dừng mở rộng** cho đến khi có API/spec đối tác rõ ràng.
- Các module trong thư mục này vẫn được import bởi `app/api/v2/public.py` và
  `app/api/v2/miniapp.py` để phục vụ flow public beta/callback đang chạy trên Mini App.
- Không mở rộng thêm bot logic mới, runtime orchestration mới, hoặc broker adapter mới ở đây.

## Phạm vi file

- `__init__.py`
- `ctrader_api_client.py`
- `ctrader_public_beta.py`

## Không được làm

- Không thêm broker adapter mới trong thư mục này.
- Không chuyển logic runtime/orchestration cốt lõi vào lane này.
- Không import từ `backend-ctrader/` (lane tách biệt, không thuộc control plane này).
- Không biến lane này thành execution path chính để đặt lệnh.
  Luồng đặt lệnh chính thuộc Windows MT5 runner, không thuộc Linux control plane.

## Điều kiện gỡ bỏ

Các file này **chưa thể xóa** vì UI cTrader đang chạy thực tế trong Mini App
vẫn dùng endpoint public beta + callback.
Chỉ xem xét gỡ khi product team duyệt ẩn/xóa lane cTrader ở `frontend-v2/`.
Trước thời điểm đó, freeze notice toàn repo và `docs/TWO_TASK_EXECUTION_PLAN.md`
là source of truth.

## Tài liệu liên quan

- `/root/spider-ai/AGENTS.md`
- `/root/spider-ai/docs/linux-backend-control-plane-mt5-directive.md`
- `/root/spider-ai/docs/TWO_TASK_EXECUTION_PLAN.md`

(`backend_ai/backend/app/legacy/LEGACY_READONLY.md` và
`backend_ai/backend/scripts/legacy/LEGACY_READONLY.md` đã bị xóa
cùng các thư mục legacy tương ứng.)
