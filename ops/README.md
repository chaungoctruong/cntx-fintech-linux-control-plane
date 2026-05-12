# `ops/` — Script tiện ích vận hành (monorepo)

Thư mục này gồm **vài script bash** dùng khi dev/kiểm tra trên máy có Docker Compose; **không** thay cho runbook đầy đủ trong `docs/` hay `DEPLOY_FRESH_VPS.md`.

## File thực tế (inventory)

| File | Việc làm |
|------|----------|
| **`preflight_linux_control_plane.sh`** | Kiểm tra read-only trước khi scale/deploy (tuỳ chọn `BACKEND_ENV_FILE=...`). Gọi từ root monorepo. |
| **`compose-dev.sh`** | Helper chạy stack dev (đọc nội dung file trước khi tin cậy). |
| **`compose-prod.sh`** | Helper compose kiểu production thử (đọc nội dung — không ngầm định là prod thật). |
| **`monitoring/check_prod_readiness.sh`** | Smoke: `docker compose ps`, `curl` `/ready` local, `curl` health public, kiểm tra catalog `/api/v2/bots`. |
| **`monitoring/README.md`** | Ghi chú ngắn cho script trên. |

## Public URL mặc định trong script

`check_prod_readiness.sh` đọc `PUBLIC_BASE_URL` từ `.env` (root); nếu trống thì fallback một URL mẫu trong script — **luôn chỉnh `.env` theo domain/tunnel thật** của bạn.

## Windows runner ↔ control-plane

- Runner production nên theo **hợp đồng** trong `CLAUDE.md` và [docs/HEADSCALE_MESH_SETUP.md](../docs/HEADSCALE_MESH_SETUP.md) (mạng riêng, Redis trên tailnet).
- Gọi API công khai qua CDN/reverse-proxy có thể gặp **timeout** cho tác vụ dài; ưu tiên đường nội bộ đã chốt trong kiến trúc deploy.

## Backup / HA

- **Không** có `ops/backup/` hay artifact backup được commit trong repo này. Backup Postgres/Redis làm theo runbook VPS (`DEPLOY_FRESH_VPS.md`) hoặc công cụ managed DB.

## Đào tạo nhân viên

1. Chạy `bash ops/preflight_linux_control_plane.sh` trên máy lab (read-only).
2. Đọc `monitoring/check_prod_readiness.sh` để biết thứ tự kiểm tra.
3. Không giả định script này tồn tại trên runner Windows — chỉ dùng trên Linux control-plane host.
