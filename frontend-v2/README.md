# Frontend Mini App

Thư mục này là frontend Next.js cho Mini App. Backend Linux sẽ serve bản build static từ `frontend-v2/out`.

## File env

Frontend dùng file:

```text
frontend-v2/.env
```

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

## Deploy lên Vercel

Vercel dùng `vercel.json` ở repo root để build `frontend-v2` và serve thư mục `frontend-v2/out`.

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
