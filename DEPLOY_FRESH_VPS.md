# Deploy Spider AI lên VPS Linux mới (Rocky 9 / RHEL family)

Chạy lần lượt từng phần. Mỗi phần có dòng **CHECK** để biết phần đó đã làm chưa. Output ở **CHECK** đúng kỳ vọng → bỏ qua phần đó.

> Mọi giá trị cần thay đổi đánh dấu `# THAY:`. Đọc trước khi paste.

---

## 0. Vào server

Nếu **đã có SSH access** (bạn đang ở terminal VPS rồi) → skip phần này.

Trên máy local, từ shell có quyền SSH:

```bash
# THAY: port + IP của bạn
ssh -p 24700 root@<TEST_BACKEND_HOST>
```

Nếu provider chỉ cho console web → vào console rồi `passwd root` set mật khẩu, sau đó SSH như trên.

---

## 1. Base packages + git

**CHECK**: `git --version` ra version → skip.

```bash
dnf update -y
dnf install -y git curl wget vim dnf-plugins-core
```

---

## 2. Clone repo

**CHECK**: `ls /root/linux-root-backend-hubot-v1/docker-compose.yml` ra path → skip.

```bash
cd /root
# THAY: URL repo (HTTPS hoặc SSH). Nếu private repo cần SSH key.
git clone <REPO_URL> linux-root-backend-hubot-v1
cd linux-root-backend-hubot-v1
```

Nếu repo private + chưa có SSH key trên server → tạo key + add vào GitHub:

**CHECK**: `ls ~/.ssh/id_ed25519` tồn tại → skip tạo key.

```bash
# THAY: email
ssh-keygen -t ed25519 -C "your-email@example.com" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
# Paste public key vào GitHub Settings -> SSH and GPG keys -> New SSH key
ssh -T git@github.com   # verify
```

---

## 3. Docker Engine + plugin compose v2

**CHECK**: `docker compose version` ra version → skip.

```bash
dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker --version && docker compose version
```

---

## 4. Cloudflared (chỉ cần nếu dùng Mini App)

Mini App Telegram bắt HTTPS. Tạm dùng Cloudflare quick tunnel cho đến khi có domain.

**CHECK**: `cloudflared --version` ra version → skip.

```bash
dnf install -y https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-x86_64.rpm
cloudflared --version
```

---

## 5. Sanity check — 3 fix đã merge trong main

3 fix bắt buộc (Dockerfile copy `ops_telegram_alerts.py`, thứ tự index `runtime_logs` trong `init_pg_schema.py`, mount `frontend-v2/out` trong `docker-compose.yml`) đã commit trong `2cf8257 fix: build error`. Lệnh dưới chỉ verify checkout đang có đủ:

```bash
cd /root/linux-root-backend-hubot-v1
git pull --ff-only

grep -q 'COPY ops_telegram_alerts.py' backend_ai/Dockerfile      && echo "fix#1 OK"  || echo "fix#1 MISSING — git pull / merge"
grep -q 'frontend-v2/out:/app/frontend-v2/out' docker-compose.yml && echo "fix#3 OK"  || echo "fix#3 MISSING — git pull / merge"
awk '/tracker\.step\("control_plane_scale_indexes"\)/{n++} END{exit !(n==1)}' \
  backend_ai/backend/init_pg_schema.py                            && echo "fix#2 OK"  || echo "fix#2 MISSING — git pull / merge"
```

Cả 3 dòng phải in `OK`. Nếu thấy `MISSING` → checkout đang ở branch/commit cũ, fix lại bằng `git fetch && git checkout main && git pull` rồi check lại.

---

## 6. Tạo `.env` (override compose)

**CHECK**: `[ -f .env ] && grep -q '^TELEGRAM_BOT_TOKEN=[0-9]' .env` → đã có token thật, skip.

```bash
# THAY: token bot Telegram (lấy từ @BotFather, KHÁC bot prod nếu prod đang chạy)
BOT_TOKEN='8768154090:AAHaqpy01dag10l2kBBaxiFfiTMjVk2KcHg'

cat > .env <<ENV
BACKEND_HOST=0.0.0.0
API_HOST=0.0.0.0
TELEGRAM_BOT_TOKEN=${BOT_TOKEN}
# PUBLIC_BASE_URL + BACKEND_URL điền sau khi mở tunnel ở phần 7
ENV
chmod 600 .env
```

---

## 7. Mở Cloudflare tunnel (nếu cần Mini App)

**CHECK**: `pgrep -f 'cloudflared tunnel'` ra PID → đã chạy, skip mở lại. Lấy URL hiện tại bằng `journalctl -u cloudflared-quick --since "10 min ago" | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1`.

Tạo systemd service để cloudflared tự khởi động lại nếu chết:

```bash
cat > /etc/systemd/system/cloudflared-quick.service <<'UNIT'
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
systemctl daemon-reload
systemctl enable --now cloudflared-quick
sleep 8

# Lấy URL random vừa cấp
TUNNEL_URL=$(journalctl -u cloudflared-quick --since "2 min ago" | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
echo "Tunnel URL: $TUNNEL_URL"

# Ghi vào .env (xoá dòng cũ nếu có rồi append)
sed -i '/^PUBLIC_BASE_URL=/d; /^BACKEND_URL=/d' .env
echo "PUBLIC_BASE_URL=${TUNNEL_URL}" >> .env
echo "BACKEND_URL=${TUNNEL_URL}" >> .env
```

> ⚠️ URL `*.trycloudflare.com` random — đổi mỗi lần restart cloudflared. Production thật: dùng domain riêng + Nginx + Let's Encrypt thay vì quick tunnel.

---

## 8. Build + start compose

**CHECK**: `docker compose ps --format json | grep -c '"State":"running"'` ra `4` → đã Up, skip `--build` chỉ cần `up -d`.

```bash
docker compose up -d --build       # lần đầu 5–15 phút
sleep 8
docker compose ps

# Mở firewall 8001 nếu muốn truy cập IP:8001 trực tiếp (bỏ qua nếu chỉ qua tunnel)
firewall-cmd --permanent --add-port=8001/tcp 2>/dev/null && firewall-cmd --reload || true

# Verify backend
curl -fsS http://127.0.0.1:8001/ready  | head -c 80; echo
curl -fsS http://127.0.0.1:8001/health | head -c 80; echo
```

---

## 9. Build frontend Mini App

**CHECK**: `[ -f frontend-v2/out/index.html ]` true → đã build. Nếu URL tunnel khác URL build cũ thì vẫn phải build lại (NEXT_PUBLIC_* inline tại build time).

```bash
TUNNEL_URL=$(grep '^PUBLIC_BASE_URL=' .env | cut -d= -f2-)

docker run --rm \
  -v "$(pwd)/frontend-v2:/app" -w /app \
  -e NEXT_PUBLIC_BACKEND_URL=${TUNNEL_URL} \
  -e NEXT_PUBLIC_API_URL=${TUNNEL_URL} \
  node:20-bookworm-slim \
  bash -c "rm -rf node_modules out .next && npm install --no-audit --no-fund && npm run build"
```

---

## 10. Force recreate spider-app (apply mount + env mới)

**CHECK**: `docker compose exec spider-app ls /app/frontend-v2/out/ | grep -q index.html` → mount đã thấy file. Skip.

```bash
docker compose up -d --force-recreate --no-deps spider-app
sleep 6

docker compose exec spider-app ls /app/frontend-v2/out/ | head -5
```

---

## 11. Verify cuối

```bash
TUNNEL_URL=$(grep '^PUBLIC_BASE_URL=' .env | cut -d= -f2-)

curl -sS -o /dev/null -w "local /        -> HTTP %{http_code}\n" http://127.0.0.1:8001/
curl -sS -o /dev/null -w "local /health  -> HTTP %{http_code}\n" http://127.0.0.1:8001/health
curl -sS -o /dev/null -w "tunnel /       -> HTTP %{http_code}\n" ${TUNNEL_URL}/
curl -sS -o /dev/null -w "tunnel /ready  -> HTTP %{http_code}\n" ${TUNNEL_URL}/ready

docker compose logs --tail=15 hubbot | grep -E "menu button|Application started|Conflict|InvalidToken"
```

Mong đợi cả 4 curl đều `200`. Hubbot log phải có `Telegram menu button configured for Mini App home.` và `Application started`. Trên Telegram `/start` bot → có nút Mini App, click vào load được trang.

---

## 12. Lệnh thường dùng sau khi up

```bash
# Trạng thái
docker compose ps
docker compose logs -f spider-app
docker compose logs -f hubbot

# Restart 1 service (đọc lại .env)
docker compose up -d spider-app hubbot

# Sau khi tunnel URL đổi: chạy lại phần 7 (lấy URL) → 9 (rebuild frontend) → 10 (force recreate)

# Tắt
docker compose down            # giữ volume db/redis
docker compose down -v         # XOÁ volume → mất DB

# Cloudflared service
systemctl status cloudflared-quick
systemctl restart cloudflared-quick
journalctl -u cloudflared-quick -f
```

---

## 13. Khi tunnel URL đổi (cloudflared restart hoặc reboot VPS)

Quick tunnel có URL random. Mỗi lần đổi:

```bash
cd /root/linux-root-backend-hubot-v1
NEW_URL=$(journalctl -u cloudflared-quick --since "5 min ago" | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
echo "URL mới: $NEW_URL"

sed -i '/^PUBLIC_BASE_URL=/d; /^BACKEND_URL=/d' .env
echo "PUBLIC_BASE_URL=${NEW_URL}" >> .env
echo "BACKEND_URL=${NEW_URL}"     >> .env

# Rebuild frontend với URL mới
docker run --rm -v "$(pwd)/frontend-v2:/app" -w /app \
  -e NEXT_PUBLIC_BACKEND_URL=${NEW_URL} \
  -e NEXT_PUBLIC_API_URL=${NEW_URL} \
  node:20-bookworm-slim \
  bash -c "rm -rf out .next && npm run build"

docker compose up -d --force-recreate --no-deps spider-app hubbot
```

Trên Telegram, user phải `/start` lại để bot set menu button mới.

---

## 14. Troubleshooting nhanh

| Triệu chứng | Nguyên nhân | Fix |
|---|---|---|
| `spider-app` exit(1) `ModuleNotFoundError: ops_telegram_alerts` | Fix #1 chưa áp | Phần 5 |
| `init_postgres_schema_failed: relation "runtime_logs" does not exist` | Fix #2 chưa áp | Phần 5 |
| Curl `/health` `Empty reply from server` | Backend bind 127.0.0.1 trong container | `BACKEND_HOST=0.0.0.0` trong `.env` |
| Hubbot `InvalidToken` | Token còn placeholder | Phần 6, set token thật |
| Hubbot `Conflict: terminated by other getUpdates` | Token đang dùng nơi khác | Tắt instance kia hoặc tạo bot mới |
| Mini App `503` + log `frontend-v2 export missing` | `frontend-v2/out` rỗng hoặc mount cũ | Phần 9 build + phần 10 force recreate |
| Bot menu báo `only https links are allowed` | `PUBLIC_BASE_URL` còn HTTP | Phần 7 đặt URL HTTPS rồi restart spider-app+hubbot |
