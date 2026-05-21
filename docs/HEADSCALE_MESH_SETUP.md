# Runbook — Headscale mesh + Redis transport (production)

**Mục đích**: thiết lập mesh VPN private giữa Linux backend ↔ tất cả Windows runner (vài trăm node), Redis chỉ bind trên mesh interface → KHÔNG expose ra public. Backend dispatch fan-out bằng Redis pipeline.

**Tại sao Headscale chứ không Tailscale free**:
- Tailscale free giới hạn 100 device → không đủ "vài trăm runner + vài nghìn user"
- Tailscale Business: $6/user × hàng nghìn = không khả thi
- Headscale = open-source server tương thích Tailscale client, **free unlimited**, self-host trên 1 VPS riêng (có thể chạy chung VPS Linux backend nếu cần tiết kiệm)

**Người thực hiện**: backend admin (Linux VPS) + 1 lần per Windows VPS (tự automation Ansible/Powershell DSC).

**Thời gian**: 1h cho server setup, 2 phút mỗi Windows VPS (script hoá được).

---

## 0. Topology

```
                ┌─ Linux Backend VPS ──────────────────────────┐
                │  - Headscale server (port 50443 TLS)         │
                │  - Tailscale client (joins own tailnet)       │
                │  - Redis (bind 100.64.0.1, password)          │
                │  - Backend (bind 100.64.0.1:8001 + 0.0.0.0    │
                │    cho Mini App qua Vercel)                   │
                └───────────────┬───────────────────────────────┘
                                │ tailnet 100.64.0.0/10 (encrypted, NAT-traversal)
                ┌───────────────┴────────────────┐
                ▼                                ▼
        Windows Runner #1                Windows Runner #N
        Tailscale client                 Tailscale client
        IP: 100.64.0.x                   IP: 100.64.0.y
        BACKEND_URL: 100.64.0.1:8001     ...
        REDIS: 100.64.0.1:6379           ...
```

Public internet: chỉ port 443 + 50443 (Headscale) trên Linux VPS. Redis + backend internal port KHÔNG bao giờ exposed.

---

## 1. Cài Headscale server (1 lần, trên Linux VPS)

```bash
# Method 1 — Docker (recommend, dễ upgrade):
mkdir -p /etc/headscale /var/lib/headscale
docker run -d --name headscale \
  --restart unless-stopped \
  -v /etc/headscale:/etc/headscale \
  -v /var/lib/headscale:/var/lib/headscale \
  -p 50443:50443 \
  -p 9090:9090 \
  headscale/headscale:0.23 \
  serve

# Tạo config tối thiểu:
cat > /etc/headscale/config.yaml <<'EOF'
server_url: https://headscale.<your-domain>:50443
listen_addr: 0.0.0.0:50443
metrics_listen_addr: 0.0.0.0:9090

private_key_path: /var/lib/headscale/private.key
noise:
  private_key_path: /var/lib/headscale/noise_private.key

ip_prefixes:
  - 100.64.0.0/10

derp:
  server:
    enabled: false
  urls:
    - https://controlplane.tailscale.com/derpmap/default

# Database/state store:
# - Lay config database theo dung version Headscale dang chay.
# - Moi truong production phai co backup/snapshot rieng cho Headscale state.
# - Khong dung chung DB voi CNTx backend.

# TLS — tự lấy cert qua Let's Encrypt nếu có domain. Hoặc dùng cert tự ký.
tls_letsencrypt_hostname: headscale.<your-domain>
tls_letsencrypt_cache_dir: /var/lib/headscale/cache
tls_letsencrypt_challenge_type: HTTP-01
tls_letsencrypt_listen: ":80"

log:
  level: info

dns_config:
  override_local_dns: false
  nameservers:
    - 1.1.1.1

policy:
  mode: file
  path: /etc/headscale/acl.hujson
EOF

# Restart container để pick config:
docker restart headscale
docker logs headscale | tail
```

**DNS cần làm trước**: A record `headscale.<your-domain> → <YOUR_HEADSCALE_VPS_PUBLIC_IP>`. Mở port **50443** + **80** (cho ACME challenge) trên VPS.

---

## 2. Tạo namespace + ACL

```bash
# 1 user (namespace) tổng = "production":
docker exec headscale headscale users create production

# Tạo pre-auth key (reusable, không expire) cho mass-onboard runner:
docker exec headscale headscale preauthkeys create \
  --user production \
  --reusable \
  --expiration 8760h \
  > /tmp/preauthkey.txt
cat /tmp/preauthkey.txt
# Copy giá trị key (dạng "tskey-auth-...") — sẽ paste vào Windows runner
```

**ACL** — kiểm soát ai gọi được ai. Tạo `/etc/headscale/acl.hujson`:

```hujson
{
  "tagOwners": {
    "tag:backend": ["production"],
    "tag:runner":  ["production"],
  },
  "acls": [
    // Backend gọi runner (vd. health probe, debug)
    { "action": "accept", "src": ["tag:backend"], "dst": ["tag:runner:*"] },
    // Runner chỉ gọi backend được — không gọi nhau (defense-in-depth)
    { "action": "accept", "src": ["tag:runner"],  "dst": ["tag:backend:6379,8001"] },
    // Backend nội bộ
    { "action": "accept", "src": ["tag:backend"], "dst": ["tag:backend:*"] },
  ],
}
```

```bash
docker restart headscale  # reload ACL
```

---

## 3. Cài Tailscale client trên Linux VPS (tự join tailnet của mình)

```bash
# Cài Tailscale client (RHEL/Rocky):
dnf install -y tailscale
systemctl enable --now tailscaled

# Join headscale với tag:backend:
tailscale up --login-server=https://headscale.<your-domain>:50443 \
  --authkey="$(cat /tmp/preauthkey.txt)" \
  --advertise-tags=tag:backend \
  --hostname=linux-control-plane

# Verify:
tailscale ip -4
# Expected: 100.64.0.1 (hoặc gần đó)

tailscale status
# Linux backend node phải hiện trong list
```

---

## 4. Bind Redis + backend trên tailnet IP

### Redis

```bash
# Lấy tailnet IP backend đã được gán:
TAILNET_IP=$(tailscale ip -4 | head -1)
echo "Backend tailnet IP: $TAILNET_IP"

# Backend đang dùng Redis trong docker compose. Update docker-compose.yml để
# expose Redis ra cả host tailnet IP (KHÔNG expose 0.0.0.0):
# Trong service 'redis', section ports:
#   ports:
#     - "${TAILNET_IP}:6379:6379"  # Bind chỉ trên tailnet
#     - "127.0.0.1:6379:6379"      # Vẫn cho local backend connect

# Hoặc dùng env-substitution. Edit docker-compose.yml redis section:
```

Sửa [docker-compose.yml](../docker-compose.yml) section `redis`:

```yaml
  redis:
    image: redis:7-alpine
    command: ["redis-server", "--requirepass", "${REDIS_PASSWORD}"]
    ports:
      - "127.0.0.1:6379:6379"          # backend local
      - "${TAILNET_IP:-127.0.0.1}:6379:6379"  # runner qua tailnet
```

```bash
# Reload với env TAILNET_IP set:
TAILNET_IP=100.64.0.1 docker compose up -d --no-deps redis

# Verify Redis chỉ accept từ tailnet + local:
ss -tlnp | grep 6379
# Expected: 127.0.0.1:6379 + 100.64.0.1:6379, KHÔNG có 0.0.0.0:6379
```

### Redis password — bắt buộc password mạnh

```bash
# Trong .env.linux:
REDIS_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)
```

Restart toàn stack để đồng bộ password:

```bash
docker compose up -d --force-recreate redis spider-app hubbot
```

---

## 5. Onboarding 1 Windows runner (script hóa được)

Trên mỗi Windows VPS, chạy 1 lần (dạng PowerShell script):

```powershell
# Step 1: Cài Tailscale Windows client
# Download: https://pkgs.tailscale.com/stable/#windows
# Hoặc qua winget:
winget install Tailscale.Tailscale -e

# Step 2: Join tailnet với pre-auth key + tag
& "C:\Program Files\Tailscale\tailscale.exe" up `
  --login-server=https://headscale.<your-domain>:50443 `
  --authkey="<PASTE PREAUTH KEY>" `
  --advertise-tags="tag:runner" `
  --hostname="runner-win-$(hostname)"

# Step 3: Verify connectivity
& "C:\Program Files\Tailscale\tailscale.exe" ping 100.64.0.1
# Expected: pong from linux-control-plane

# Step 4: Test Redis from Windows (nếu có redis-cli):
# redis-cli -h 100.64.0.1 -p 6379 -a "<PASSWORD>" PING
# Expected: PONG
```

Với vài trăm runner, **tự động hoá**:
- Đóng gói thành 1 PowerShell script + bake pre-auth key vào image VPS template
- Hoặc Ansible playbook (Windows targets) — `ansible.windows.win_chocolatey` install Tailscale + `ansible.windows.win_command tailscale up`

---

## 6. Update Windows runner config

Sau khi node lên tailnet, đổi env runner — gửi cho Windows team:

```env
# CŨ (public URL):
# BACKEND_URL=https://<YOUR_PUBLIC_CONTROL_PLANE_HOST>
# RUNNER_TRANSPORT=redis_queue
# (Redis phải reach được từ Windows)

# MỚI:
BACKEND_URL=http://100.64.0.1:8001
CONTROL_PLANE_URL=http://100.64.0.1:8001
RUNNER_TRANSPORT=redis_queue
REDIS_HOST=100.64.0.1
REDIS_PORT=6379
REDIS_PASSWORD=<password ở step 4>
REDIS_DB=0
BACKEND_API_KEY=<unchanged>
```

Restart node_control để pick env. Xem [WINDOWS_RUNNER_HANDOFF_PROMPT.md](WINDOWS_RUNNER_HANDOFF_PROMPT.md).

---

## 7. Verify end-to-end

Trên Linux backend:

```bash
# 7.1 Backend gửi 1 fake command vào runner queue (smoke):
RUNNER_ID=runner-win-test-01
docker compose exec -T redis redis-cli -a "$REDIS_PASSWORD" \
  LPUSH "mt5:runner:${RUNNER_ID}:commands" '{"command_id":"smoke-001","command_type":"PLACE_ORDER","payload":{"smoke":true}}'

# 7.2 Trên Windows runner, watch log:
# Get-Content C:\path\to\runner\logs\node_control.log -Wait | Select-String "smoke-001"
# Expected: thấy command đến trong vài chục ms
```

Smoke test fan-out qua endpoint mới:

```bash
curl -X POST http://100.64.0.1:8001/api/v2/public/tradingview/broadcast \
  -H "Content-Type: application/json" \
  -H "X-TradingView-Secret: $TRADINGVIEW_WEBHOOK_SECRET" \
  -d '{
    "alert_id":"smoke-bcast-001",
    "signal_id":"strategy_test",
    "action":"BUY",
    "symbol":"EURUSD",
    "default_volume":0.01
  }' | jq

# Expected response: {subscribers_total: N, dispatched: N, deduped: 0, failed: 0, results: [...]}
# Mỗi runner trong tailnet phải nhận PLACE_ORDER trong vài trăm ms.
```

---

## 8. Operational

### Monitoring

```bash
# Số node hiện trong tailnet:
docker exec headscale headscale nodes list

# Health Headscale:
curl https://headscale.<your-domain>:50443/health

# Redis backlog per runner:
docker compose exec -T redis redis-cli -a "$REDIS_PASSWORD" \
  EVAL "local total=0; for _,k in ipairs(redis.call('KEYS','mt5:runner:*:commands')) do total=total+redis.call('LLEN',k) end; return total" 0
```

### Onboard new runner (steady state)

```bash
# Tạo pre-auth key mới (one-shot):
docker exec headscale headscale preauthkeys create --user production --expiration 24h
# Gửi key qua kênh an toàn cho team setup runner mới
```

### Revoke runner (compromise/decommission)

```bash
docker exec headscale headscale nodes list  # tìm node ID
docker exec headscale headscale nodes delete --identifier <node_id>
# Node sẽ bị kick khỏi mesh trong 1 phút.
```

### Backup Headscale state

```bash
# Headscale state + keys:
tar czf /backup/headscale-$(date +%F).tgz /etc/headscale /var/lib/headscale
# Restore: extract về same paths, restart container
```

---

## 9. Rollback

Nếu sau khi cutover gặp issue lớn:

1. Stop tailscale trên Windows runner:
   ```powershell
   & "C:\Program Files\Tailscale\tailscale.exe" down
   ```
2. Revert env runner (ví dụ `BACKEND_URL` công khai cũ) — **giữ** `RUNNER_TRANSPORT=redis_queue` và `REDIS_*` tới Redis còn reach được.
3. Restart node_control
4. Nếu Redis không tới được từ Windows, lệnh sẽ không xuống runner cho tới khi sửa mạng hoặc cấu hình Redis.
5. Báo backend admin tìm root cause trên Headscale/Redis trước khi cutover lại

---

## 10. Troubleshooting

| Triệu chứng | Nguyên nhân | Fix |
|---|---|---|
| `tailscale up` báo "auth failed" | Pre-auth key expired/đã dùng (nếu single-use) | Gen key mới với `--reusable` |
| Windows runner ping `100.64.0.1` timeout | Firewall Windows hoặc Headscale chưa accept node | `docker exec headscale headscale nodes list` xem có pending không |
| Redis PING fail từ runner | Redis chưa bind tailnet IP hoặc password sai | `ss -tlnp | grep 6379` trên Linux + verify env REDIS_PASSWORD đồng bộ |
| Headscale TLS fail | Cert ACME chưa lấy được | Mở port 80 + DNS đúng + restart container |
| Backend không thấy command runner BRPOP | Runner code chưa switch sang `redis_queue` mode | Verify `RUNNER_TRANSPORT=redis_queue` trong env runner |
| Broadcast endpoint trả `subscribers_total: 0` | Chưa INSERT subscription rows | `INSERT INTO tradingview_signal_subscriptions (...) VALUES (...);` (xem schema mới) |
