# Backend Linux Control-Plane

Thư mục này là backend FastAPI của hệ thống CNTx Labs MT5 SaaS. Backend Linux là nơi điều phối chính: quản lý catalog bot, user/account/deployment, runner register/heartbeat, package handoff, audit và các API `/api/v2/*`.

## Vai trò của backend

- Nhận request từ frontend, hubbot và Windows runner.
- Lưu dữ liệu vào Postgres và dùng Redis cho queue/cache nội bộ.
- Quản lý `bot_catalog`, trong đó `gsalgovip` hiện là bot `backend_webhook_signal`.
- Tạo package/deployment payload để Windows runner hiểu bot nào được hỗ trợ.
- Không mở MT5, không gọi `order_send`, không chạy TradingView webhook trên Windows.

## File env dùng khi nào

| File | Dùng cho | Ghi chú |
|---|---|---|
| `../../.env` | Docker Compose trên Linux | Đây là file chính khi chạy `docker compose up -d` |
| `.env` | Chạy backend trực tiếp trên host | Chỉ dùng khi không chạy qua compose |
| `../../frontend-v2/.env` | Frontend build/chạy riêng | Biến frontend được inline lúc build |

Không commit các file `.env`. Không in hoặc dán password, token, API key vào log/chat/tài liệu.

## Cấu hình product-style hiện tại

Khi chạy bằng Docker Compose:

- Backend bind trong container bằng `BACKEND_HOST=0.0.0.0`.
- Public API cho máy ngoài gọi vào là `PUBLIC_BASE_URL` và `RUNNER_CONTROL_PLANE_URL`.
- Hubbot gọi backend qua Docker network bằng `BACKEND_URL=http://spider-app:8001`.
- Postgres/Redis dùng service nội bộ `db` và `redis`.

Windows runner chỉ cần gọi HTTP về backend. Windows không cần cấu hình `POSTGRES_*`, `DATABASE_URL` hoặc `DB_MODE`.

## Checklist trước khi vận hành thử

Chỉ đọc trạng thái, không gửi lệnh trade:

```bash
curl -fsS http://127.0.0.1:8001/ready
curl -fsS http://127.0.0.1:8001/api/v2/system/healthz
docker compose ps
docker compose logs --tail=100 spider-app
```

Điều kiện tối thiểu:

- `/ready` trả `ok=true`.
- Postgres và Redis healthy.
- `runner-win-test-01` hoặc runner đang test online/fresh.
- Queue command/processing sạch trước khi test lifecycle.
- `bot_catalog` giữ contract `gsalgovip@0.3.0` với `required_params`, `risk_contract`, `resource_hints`, `bot_type`, `execution_owner`, `windows_role`.

## Ranh giới an toàn

Không làm các việc này nếu chưa có phase/rundown rõ ràng:

- Không gửi `START_BOT` hoặc `STOP_BOT` production.
- Không gửi `PLACE_ORDER`, `MODIFY_ORDER`, `CLOSE_ORDER`.
- Không gọi `EXECUTE_SIGNAL_BATCH` khi chưa sang phase batch.
- Không mở MT5 từ Linux.
- Không sửa account/deployment live khi chỉ đang kiểm catalog hoặc readiness.
