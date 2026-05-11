# Deploy Spider AI lên VPS Linux mới (Rocky 9 / RHEL family)

Runbook cho **VPS mới** hoặc **môi trường cô lập**. Mỗi mục có **CHECK** để bỏ qua bước đã làm.

**An toàn dữ liệu**

- Không dán token, password, API key thật vào file hay chat. Dùng biến môi trường cục bộ hoặc secret manager.
- File `.env` runtime phải `chmod 600`, không commit.
- `docker compose down -v` **xoá volume Postgres/Redis** — chỉ trong cửa sổ bảo trì có chủ đích.

**Mục lục**

1. [Vào server](#0-vào-server)
2. [Base packages + git](#1-base-packages--git)
3. [Clone repo](#2-clone-repo)
4. [Docker Engine + Compose v2](#3-docker-engine--plugin-compose-v2)
5. [Cloudflared (tuỳ chọn, Mini App)](#4-cloudflared-chỉ-cần-nếu-dùng-mini-app)
6. [Sanity check — 3 fix đã merge](#5-sanity-check--3-fix-đã-merge-trong-main)
7. [Tạo `.env` (override compose)](#6-tạo-env-override-compose)
8. [Tunnel HTTPS (tuỳ chọn)](#7-mở-cloudflare-tunnel-nếu-cần-mini-app)
9. [Build + start compose](#maintenance-window-only--build--start-compose)
10. [Build frontend Mini App](#maintenance-window-only--build-frontend-mini-app)
11. [Recreate spider-app (mount + env)](#maintenance-window-only--force-recreate-spider-app)
12. [Verify cuối](#12-verify-cuối-read-only)
13. [Lệnh vận hành sau khi up](#13-lệnh-vận-hành-sau-khi-up)
14. [Khi tunnel URL đổi](#maintenance-window-only--khi-tunnel-url-đổi)
15. [Troubleshooting](#15-troubleshooting-nhanh)

---

## 0. Vào server

**CHECK**: Đang SSH vào VPS đích → skip.

```bash
# THAY: port + IP
ssh -p 24700 root@<TEST_BACKEND_HOST>
```

---

## 1. Base packages + git

**CHECK**: `git --version` in ra version → skip.

```bash
dnf update -y
dnf install -y git curl wget vim dnf-plugins-core
```

---

## 2. Clone repo

**CHECK**: `test -f /root/linux-root-backend-hubot-v1/docker-compose.yml` → skip.

```bash
cd /root
# THAY: URL repo (HTTPS hoặc SSH)
git clone <REPO_URL> linux-root-backend-hubot-v1
cd linux-root-backend-hubot-v1
```

SSH key (repo private): tạo key, add vào GitHub/GitLab, `ssh -T` verify — bỏ qua nếu đã có.

---

## 3. Docker Engine + plugin compose v2

**CHECK**: `docker compose version` in ra version → skip.

```bash
dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker --version && docker compose version
```

> `systemctl enable --now` có tác động dịch vụ — chỉ trên VPS mới hoặc trong cửa sổ bảo trì.

---

## 4. Cloudflared (chỉ cần nếu dùng Mini App)

**CHECK**: `cloudflared --version` → skip.

```bash
dnf install -y https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-x86_64.rpm
cloudflared --version
```

---

## 5. Sanity check — 3 fix đã merge trong main

```bash
cd /root/linux-root-backend-hubot-v1
git pull --ff-only

grep -q 'COPY ops_telegram_alerts.py' backend_ai/Dockerfile      && echo "fix#1 OK"  || echo "fix#1 MISSING — git pull / merge"
grep -q 'frontend-v2/out:/app/frontend-v2/out' docker-compose.yml && echo "fix#3 OK"  || echo "fix#3 MISSING — git pull / merge"
awk '/tracker\.step\("control_plane_scale_indexes"\)/{n++} END{exit !(n==1)}' \
  backend_ai/backend/init_pg_schema.py                            && echo "fix#2 OK"  || echo "fix#2 MISSING — git pull / merge"
```

Cả 3 dòng phải `OK`. Nếu `MISSING` → `git fetch && git checkout main && git pull` rồi kiểm tra lại.

---

## 6. Tạo `.env` (override compose)

**CHECK**: `[ -f .env ] && grep -q '^TELEGRAM_BOT_TOKEN=' .env` với token thật đã set → skip phần tạo mới (chỉ bổ sung biến thiếu).

**Không lưu token thật trong tài liệu.** Tạo token qua [@BotFather](https://t.me/BotFather). Nhập token trên máy bạn (stdin), không ghi vào shell history:

```bash
cd /root/linux-root-backend-hubot-v1
set -o history off
read -r -s -p "Paste TELEGRAM_BOT_TOKEN (hidden): " BOT_TOKEN
echo
set -o history on

umask 077
cat > .env <<ENV
BACKEND_HOST=0.0.0.0
API_HOST=0.0.0.0
TELEGRAM_BOT_TOKEN=${BOT_TOKEN}
# Hubbot trong Docker Compose nên gọi backend qua service nội bộ (tránh loop qua tunnel).
BACKEND_URL=http://spider-app:8001
# Bổ sung sau khi có HTTPS: PUBLIC_BASE_URL, RUNNER_CONTROL_PLANE_URL, REDIS_URL, BACKEND_API_KEY, LOCAL_* — xem README.md mục biến môi trường.
ENV
chmod 600 .env
```

**Ghi chú `docker-compose.yml`**: Compose yêu cầu `LOCAL_REDIS_PASSWORD` (và có thể các biến `LOCAL_POSTGRES_*`) trong cùng file env — đối chiếu comment trong `docker-compose.yml` và [README.md](README.md).

---

## 7. Mở Cloudflare tunnel (nếu cần Mini App)

**CHECK**: Đã có URL HTTPS cố định (domain + TLS) hoặc quick tunnel đang chạy → chỉ cập nhật `.env`.

Quick tunnel (URL random, đổi khi restart process):

```bash
sudo tee /etc/systemd/system/cloudflared-quick.service >/dev/null <<'UNIT'
[Unit]
Description=Cloudflared quick tunnel to backend:8001
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/cloudflared tunnel --url http://localhost:8001
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared-quick
sleep 8
TUNNEL_URL=$(sudo journalctl -u cloudflared-quick --since "2 min ago" | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
echo "Tunnel URL: $TUNNEL_URL"
```

Ghi HTTPS vào `.env` — **không** ghi đè `BACKEND_URL` (giữ `http://spider-app:8001` cho hubbot):

```bash
cd /root/linux-root-backend-hubot-v1
sed -i '/^PUBLIC_BASE_URL=/d; /^RUNNER_CONTROL_PLANE_URL=/d' .env
grep -q '^PUBLIC_BASE_URL=' .env || echo "PUBLIC_BASE_URL=${TUNNEL_URL}" >> .env
grep -q '^RUNNER_CONTROL_PLANE_URL=' .env || echo "RUNNER_CONTROL_PLANE_URL=${TUNNEL_URL}" >> .env
```

> Production: ưu tiên domain + Nginx + Let's Encrypt thay vì `*.trycloudflare.com`.

---

## Maintenance window only — Build + start compose

**CHECK**: Bốn service đều `running` → chỉ cần xem log / health.

```bash
cd /root/linux-root-backend-hubot-v1
docker compose up -d --build
sleep 8
docker compose ps

firewall-cmd --permanent --add-port=8001/tcp 2>/dev/null && firewall-cmd --reload || true
```

---

## Maintenance window only — Build frontend Mini App

**CHECK**: `[ -f frontend-v2/out/index.html ]` và URL `NEXT_PUBLIC_*` vẫn đúng → skip.

```bash
cd /root/linux-root-backend-hubot-v1
TUNNEL_URL=$(grep '^PUBLIC_BASE_URL=' .env | cut -d= -f2-)

docker run --rm \
  -v "$(pwd)/frontend-v2:/app" -w /app \
  -e NEXT_PUBLIC_BACKEND_URL="${TUNNEL_URL}" \
  -e NEXT_PUBLIC_API_URL="${TUNNEL_URL}" \
  node:20-bookworm-slim \
  bash -c "rm -rf node_modules out .next && npm install --no-audit --no-fund && npm run build"
```

**Node**: image `node:20-bookworm-slim` khớp khuyến nghị Node 20 LTS (xem [README.md](README.md)).

---

## Maintenance window only — Force recreate spider-app

**CHECK**: `docker compose exec spider-app test -f /app/frontend-v2/out/index.html` → skip.

```bash
cd /root/linux-root-backend-hubot-v1
docker compose up -d --force-recreate --no-deps spider-app
sleep 6
docker compose exec spider-app ls /app/frontend-v2/out/ | head -5
```

---

## 12. Verify cuối (read-only)

```bash
cd /root/linux-root-backend-hubot-v1
TUNNEL_URL=$(grep '^PUBLIC_BASE_URL=' .env | cut -d= -f2-)

curl -sS -o /dev/null -w "local /        -> HTTP %{http_code}\n" http://127.0.0.1:8001/
curl -sS -o /dev/null -w "local /health  -> HTTP %{http_code}\n" http://127.0.0.1:8001/health
curl -sS -o /dev/null -w "local /ready  -> HTTP %{http_code}\n" http://127.0.0.1:8001/ready
curl -sS -o /dev/null -w "tunnel /       -> HTTP %{http_code}\n" "${TUNNEL_URL}/"
curl -sS -o /dev/null -w "tunnel /ready  -> HTTP %{http_code}\n" "${TUNNEL_URL}/ready"

docker compose logs --tail=15 hubbot | grep -E "menu button|Application started|Conflict|InvalidToken" || true
```

Kỳ vọng: các `curl` trả `200` (hoặc `000` nếu tunnel chưa sẵn sàng). Hubbot: menu Mini App HTTPS + `Application started`.

---

## 13. Lệnh vận hành sau khi up

### Read-only / an toàn

```bash
docker compose ps
docker compose logs --tail=80 spider-app
docker compose logs --tail=80 hubbot
curl -fsS http://127.0.0.1:8001/ready | head -c 200; echo
systemctl status cloudflared-quick 2>/dev/null || true
```

### Maintenance window only (restart / recreate)

```bash
docker compose up -d spider-app hubbot
docker compose restart hubbot
docker compose up -d --force-recreate --no-deps spider-app
sudo systemctl restart cloudflared-quick
sudo journalctl -u cloudflared-quick -n 50 --no-pager
```

### Destructive — chỉ khi chủ đích reset môi trường

```bash
docker compose down
docker compose down -v
```

`down -v` **xoá volume** Postgres + Redis.

---

## Maintenance window only — Khi tunnel URL đổi

```bash
cd /root/linux-root-backend-hubot-v1
NEW_URL=$(sudo journalctl -u cloudflared-quick --since "5 min ago" | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
echo "URL mới: $NEW_URL"

sed -i '/^PUBLIC_BASE_URL=/d; /^RUNNER_CONTROL_PLANE_URL=/d' .env
echo "PUBLIC_BASE_URL=${NEW_URL}" >> .env
echo "RUNNER_CONTROL_PLANE_URL=${NEW_URL}" >> .env

docker run --rm -v "$(pwd)/frontend-v2:/app" -w /app \
  -e NEXT_PUBLIC_BACKEND_URL="${NEW_URL}" \
  -e NEXT_PUBLIC_API_URL="${NEW_URL}" \
  node:20-bookworm-slim \
  bash -c "rm -rf out .next && npm run build"

docker compose up -d --force-recreate --no-deps spider-app hubbot
```

User Telegram nên `/start` lại để cập nhật menu button.

---

## 15. Troubleshooting nhanh

| Triệu chứng | Nguyên nhân | Gợi ý |
|-------------|----------------|-------|
| `spider-app` exit(1) `ModuleNotFoundError: ops_telegram_alerts` | Thiếu copy trong image | Mục 5 |
| `init_postgres_schema_failed: relation "runtime_logs" does not exist` | Schema init cũ | Mục 5, cập nhật code |
| Curl `/health` `Empty reply from server` | Bind sai trong container | `BACKEND_HOST=0.0.0.0` trong `.env` |
| Hubbot `InvalidToken` | Token sai/placeholder | Mục 6 |
| Hubbot `Conflict: terminated by other getUpdates` | Hai process cùng token | Một bot một consumer |
| Mini App `503` + export missing | Chưa build `frontend-v2/out` | Mục build frontend |
| `only https links are allowed` | `PUBLIC_BASE_URL` không HTTPS | Mục 7 |

---

## Rollback (tài liệu / repo)

- Chỉ rollback **git** cho thay đổi doc/local: `git checkout -- DEPLOY_FRESH_VPS.md` (và file liên quan).
- **Không** rollback Postgres/Redis/production state bằng tay từ runbook này.
