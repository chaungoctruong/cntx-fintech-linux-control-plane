# Frontend Mini App

Thư mục này là frontend Next.js cho Mini App. Backend Linux serve bản build static từ **`frontend-v2/out`** (mount trong `docker-compose.yml`).

**Telegram `web_app`:** menu và URL Mini App trên Telegram phải là **HTTPS** (production / tunnel). Các ví dụ `http://...` dưới đây chỉ phù hợp **dev nội bộ** (LAN, curl); đừng dùng HTTP công khai cho user Telegram.

## File env

Frontend dùng file:

```text
frontend-v2/.env
```

Sao chép từ mẫu (commit được): `cp .env.example .env` trong thư mục `frontend-v2/`, rồi chỉnh URL.

Biến quan trọng:

```env
NEXT_PUBLIC_BACKEND_URL=http://<linux-ip-hoac-domain>:8001
NEXT_PUBLIC_API_URL=http://<linux-ip-hoac-domain>:8001
```

Các biến `NEXT_PUBLIC_*` được nhúng vào bundle khi build. Vì vậy, mỗi lần đổi IP/domain backend thì phải build lại frontend.

## Build frontend

Nên build bằng container Linux để tránh lỗi path trên Windows:

```bash
docker run --rm \
  -v "$PWD/frontend-v2:/app" \
  -w /app \
  -e NEXT_PUBLIC_BACKEND_URL=http://<linux-ip-hoac-domain>:8001 \
  -e NEXT_PUBLIC_API_URL=http://<linux-ip-hoac-domain>:8001 \
  node:20-bookworm-slim \
  bash -c "rm -rf node_modules out .next && npm install --no-audit --no-fund && npm run build"
```

Sau khi build, backend Docker Compose sẽ đọc `frontend-v2/out` qua volume mount.

## Deploy lên Vercel (tuỳ chọn)

File **`vercel.json` ở root monorepo** (cùng cấp với `frontend-v2/`) định nghĩa `installCommand` / `buildCommand` / `outputDirectory` và **`rewrites`** tới backend Linux. **`destination`** phải là host/port mà **Vercel edge gọi được** (thường public IP VPS + `8001` cho API, `8081` cho webhook hubbot). Đổi toàn bộ khi đổi máy hoặc bảo vệ bằng tunnel HTTPS riêng. Clone/fork công khai: **không** giữ IP/domain của môi trường khác — thay bằng placeholder (vd. `192.0.2.1` RFC 5737) rồi cấu hình lại trước deploy.

File **`frontend-v2/vercel.json`** chỉ dùng khi project Vercel gốc là thư mục `frontend-v2/` (cùng quy tắc `rewrites`).

Khi chạy qua domain Vercel:

- Frontend nên gọi API tương đối qua `/api/v2/...`.
- `vercel.json` rewrite `/api/v2/*` về Linux backend public.
- Không set `NEXT_PUBLIC_BACKEND_URL` thành URL `http://...` trong production Vercel, vì trang HTTPS gọi HTTP trực tiếp sẽ dễ bị browser chặn mixed content.
- Sau khi có domain HTTPS riêng cho backend Linux, đổi rewrite sang domain HTTPS đó.

## Checklist kiểm tra

```bash
test -d frontend-v2/out/_next
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/
```

Kỳ vọng:

- `frontend-v2/out/_next` tồn tại.
- Backend trả HTTP `200` cho trang Mini App.
- Frontend đang gọi đúng backend test/product theo `NEXT_PUBLIC_BACKEND_URL`.

## Quy tắc an toàn

- Không để secret trong frontend. Mọi biến `NEXT_PUBLIC_*` đều là public.
- Không dùng file example làm runtime chính.
- Không quên rebuild khi đổi URL backend.
