# `config/` — Cấu hình handoff (Nginx mẫu)

Thư mục này chứa **một file cấu hình Nginx mẫu** dùng khi bàn giao hoặc ghép vào edge thật.

## File trong thư mục

| File | Việc làm |
|------|----------|
| **`nginx-spider.conf`** | Mẫu reverse proxy / static cho control-plane một node (chỉnh `server_name`, upstream, TLS theo môi trường). |

## File ở cấp monorepo (liên quan)

| File | Việc làm |
|------|----------|
| **`../nginx.conf`** | Baseline sample ở root repo — có thể là bản local/legacy; không tự động đồng bộ với `nginx-spider.conf`. |

## Mẫu HA / edge khác

Nếu team cần **mẫu HA** (multi-node, include split), đặt trong repo deploy riêng hoặc `docs/` theo manifest — **hiện không có** thư mục `ops/ha/` trong monorepo này.

## File env runtime (tham chiếu)

| File | Khi nào |
|------|---------|
| **`../.env`** | Docker Compose trên Linux — thường là file env chính. |
| **`../backend_ai/backend/.env`** | Chạy backend trực tiếp trên host (không qua Compose). |
| **`../frontend-v2/.env`** | Build Mini App (`NEXT_PUBLIC_*` nhúng lúc build). |

Không commit secret. Không dán token/password vào README hay ticket công khai.

## Quy tắc khi sửa cấu hình

- Sửa đúng file mà dịch vụ thật đang `include` / mount.
- Không chỉnh Nginx khi chỉ đổi credential DB/Redis — đổi env/service tương ứng.
- Trước khi reload production: `nginx -t` (hoặc `docker compose config --quiet` cho compose).
