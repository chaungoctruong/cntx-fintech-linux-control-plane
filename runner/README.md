# Runner Shared Schemas

## Mục tiêu thư mục
- Cung cấp **hợp đồng dữ liệu dùng chung** giữa các thành phần (backend, runner runtime, các consumer/event pipeline).
- Chuẩn hóa cấu trúc `command` và `event` để các bên giao tiếp nhất quán, giảm lỗi mapping.
- Hỗ trợ onboarding nhân viên mới: nhìn một nơi là hiểu định dạng dữ liệu chuẩn của runner.

## Nhiệm vụ chính
- Định nghĩa `bot_catalog.py`:
  - Scan `BOT_TRADING_ROOT` / `bot-trading/` để tìm package có `bot_manifest.json`.
  - Validate contract cơ bản v1 và xuất payload `available_bots` + `bot_catalog` cho register/heartbeat Windows Phase 1.
  - Không import/chạy runtime bot, không mở MT5, không mở webhook.
- Định nghĩa schema lệnh trong `schemas/commands.py`:
  - Enum loại lệnh (`START_BOT`, `STOP_BOT`, `PLACE_ORDER`, ...).
  - Model `RunnerCommand` với validate bắt buộc (account, deployment, runner, slot, trace...).
- Định nghĩa schema sự kiện trong `schemas/events.py`:
  - Enum loại sự kiện (`BOT_STARTED`, `ORDER_FILLED`, `HEARTBEAT`, ...).
  - Model `RunnerEvent` với `severity`, `payload`, `trace_id`, `command_id`.
- Export tập trung qua `schemas/__init__.py` để import thống nhất.

## Hành vi chuẩn bắt buộc
- Kiểm tra catalog cục bộ (khi đã có package dưới `bot-trading/` với `bot_manifest.json`):
  `python -m runner.bot_catalog --root bot-trading --expect-bot <code> --expect-version <semver từ manifest>`.
- Mọi dữ liệu lệnh/sự kiện đi qua biên hệ thống phải map về các schema trong thư mục này.
- Không tự ý thêm trường ngoài schema ở từng service riêng lẻ; nếu cần mở rộng thì cập nhật schema gốc trước.
- Dùng enum chuẩn thay vì string tự do để tránh sai chính tả và lệch nghiệp vụ giữa các hệ.
- Luôn giữ tương thích ngược khi thay đổi schema đã dùng trong production.

## Logic vận hành cần nắm
- `RunnerCommand` là đầu vào điều khiển runner:
  - Xác thực dữ liệu tối thiểu trước khi đẩy xuống runtime.
  - Có alias chuẩn hóa (`cmd_type`, `requested_cmd_type`) để tương thích các lane runtime cũ.
- `RunnerEvent` là đầu ra trạng thái/thực thi:
  - Chuẩn hóa `event_type` (hỗ trợ normalize trước khi validate).
  - Mang `trace_id`/`command_id` để truy vết end-to-end từ command đến event.

## Quy tắc khi chỉnh sửa
- Mỗi thay đổi schema phải trả lời rõ:
  - Vì sao cần đổi?
  - Ảnh hưởng ngược đến consumer cũ là gì?
  - Kế hoạch rollout/migration nếu có breaking change.
- Nếu thêm enum mới, phải cập nhật tài liệu nghiệp vụ liên quan và nơi consume chính.
- Tránh thay đổi tên field đang dùng rộng rãi; ưu tiên thêm field mới + deprecate có lộ trình.

## Checklist review cho nhân viên mới
- Đã dùng đúng model `RunnerCommand` / `RunnerEvent` chưa?
- Có đảm bảo đủ trường bắt buộc (`command_id`, `deployment_id`, `runner_id`, `trace_id`, ...) chưa?
- Có dùng đúng enum type thay vì string hardcode chưa?
- Có xét tác động backward compatibility trước khi merge chưa?
