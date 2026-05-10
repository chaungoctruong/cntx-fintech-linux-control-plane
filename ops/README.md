# Vận hành production thử

Thư mục này chứa runbook và script vận hành cho Linux backend khi chạy product-style bằng Docker Compose.

## Ranh giới hiện tại

- Đây là cấu hình production thử một node, chưa phải HA thật.
- Backend, Postgres và Redis chạy trên cùng VPS bằng Docker Compose.
- Frontend/Mini App dùng Vercel HTTPS tại `https://work-mu-five.vercel.app`.
- Windows runner nên gọi control-plane qua `https://work-mu-five.vercel.app`, không gọi IP HTTP trực tiếp.

## Việc đã chuẩn hóa

- `APP_ENV=production` để backend bật kiểm tra secret production.
- `SERVICE_MODE=production` để tránh nhầm với local/dev.
- Redis nội bộ có password và AOF persistence.
- Docker Compose có healthcheck cho Postgres, Redis và backend.
- Có script backup Postgres/Redis.
- Có script kiểm tra readiness/public API/catalog.

## Việc vẫn phải làm trước production thật

- Rotate token Telegram/Gemini ở nhà cung cấp nếu token từng bị lộ trong chat/log.
- Thêm backup offsite, ví dụ đẩy file backup sang object storage.
- Thêm monitoring ngoài VPS, ví dụ uptime check, alert Telegram, disk usage, CPU/RAM.
- Chốt quy trình release bằng commit/tag rõ ràng.
- Nếu nhận live-money traffic, nên tách Postgres/Redis sang managed hoặc HA.

## Lệnh kiểm tra nhanh

```bash
bash ops/monitoring/check_prod_readiness.sh
```

## Lệnh backup

```bash
bash ops/backup/backup_postgres.sh
bash ops/backup/backup_redis.sh
```

File backup tạo trong `ops/artifacts/backups/`, thư mục này đã được git ignore.

## Runbook runner khi slot bị kẹt

- Nếu Windows báo slot cũ `LISTENING` nhưng Linux deployment đã `stopped`, ưu tiên drain runner trước khi dọn.
- Gửi `STOP_BOT` cleanup đúng deployment bị kẹt, không clear toàn bộ Redis và không sửa slot/account live khác.
- Chỉ resume runner khi inventory đã sạch: slot cũ về `READY/EMPTY`, queue `commands`, `commands_processing`, `verification`, `verification_processing` đều bằng `0`.
- Sau cleanup chỉ start lại một deployment test/đang vận hành thật cần thiết, rồi xác nhận chỉ có một slot `LISTENING`.

## AI hiện tại

- Trading/go-live hiện không phụ thuộc `ai_available`.
- `AI_PROVIDER=ollama` đang được defer nếu máy chưa có Ollama/model sẵn sàng.
- Khi cần bật AI production, chọn một hướng rõ ràng: cài Ollama model trên server hoặc chuyển sang Gemini/API provider rồi test riêng.

## Public health

- Vercel phải proxy `/ready` và `/health` về Linux backend để monitor ngoài server kiểm tra được.
- `/api/v2/*` vẫn giữ nguyên contract runner/control-plane hiện có.
