# Prompt cho Windows Runner Team — chuyển sang Headscale mesh + Redis transport

> **Cách dùng**: copy block giữa 2 dòng `---PROMPT START---` / `---PROMPT END---` gửi cho team Windows runner. Họ paste vào Claude/ChatGPT trong môi trường repo `windowns-runner-mt5-user-v1`.

---PROMPT START---

# Yêu cầu Windows runner — chuyển sang Headscale + Redis transport

## 1. Bối cảnh kiến trúc mới

Backend Linux đã setup **Headscale mesh VPN** (Tailscale tự host) cho production scale "vài trăm runner + vài nghìn user". Mỗi Windows VPS sẽ:

1. Cài Tailscale client → join tailnet private
2. Connect backend qua tailnet IP `100.64.0.1`
3. **Switch transport từ HTTP long-poll → Redis BRPOP** — đây là điểm quan trọng nhất

**Lý do**:
- HTTP long-poll qua Vercel timeout 10s → 502 (vấn đề hiện tại)
- HTTP qua subdomain TLS cũng OK nhưng latency 0-10s
- Redis push: dispatch latency <100ms, fan-out 1 TradingView signal → 100 user trong 1 batch ~50ms
- Tailnet private = no public exposure, tự động encryption + ACL

**Dispatch flow mới (production target)**:
```
TradingView alert
  → POST /api/v2/public/tradingview/broadcast (qua Vercel hoặc tailnet)
  → backend SELECT subscribers (vài nghìn user) WHERE signal_id=X
  → Redis pipeline LPUSH N commands → tất cả runner queue
  → mỗi runner BRPOP "mt5:runner:{RUNNER_ID}:commands"
  → worker thi hành lệnh PLACE_ORDER trên MT5
```

## 2. Việc cần làm

### Phase A — Cài Tailscale + join tailnet (1 lần per VPS)

PowerShell (chạy với admin):

```powershell
# A1. Cài Tailscale client
winget install Tailscale.Tailscale -e
# Hoặc download .exe: https://pkgs.tailscale.com/stable/#windows

# A2. Join tailnet với pre-auth key (Linux team sẽ gửi qua kênh an toàn)
$PREAUTH_KEY = "<KEY-GỬI-RIÊNG>"
& "C:\Program Files\Tailscale\tailscale.exe" up `
  --login-server=https://headscale.cntxlabs.com:50443 `
  --authkey="$PREAUTH_KEY" `
  --advertise-tags="tag:runner" `
  --hostname="runner-win-$(hostname)"

# A3. Verify connectivity tới backend trong tailnet
& "C:\Program Files\Tailscale\tailscale.exe" ping 100.64.0.1
# Expected: pong from cntxlabs-backend (vài chục ms)

# A4. Test Redis từ Windows (cần redis-cli — có thể bỏ qua nếu node_control tự test)
# redis-cli -h 100.64.0.1 -p 6379 -a "<REDIS_PASSWORD>" PING
# Expected: PONG
```

### Phase B — Switch transport sang `redis_queue`

Đổi env runner — file `runner/.env` hoặc tương đương:

```env
# CŨ:
# BACKEND_URL=https://cntxlabs.vercel.app
# RUNNER_TRANSPORT=http_poll

# MỚI:
BACKEND_URL=http://100.64.0.1:8001
CONTROL_PLANE_URL=http://100.64.0.1:8001
RUNNER_TRANSPORT=redis_queue
REDIS_HOST=100.64.0.1
REDIS_PORT=6379
REDIS_PASSWORD=<gửi riêng>
REDIS_DB=0
BACKEND_API_KEY=<unchanged — giữ key cũ>
```

**Note quan trọng cho runner code**:
- HTTP endpoint `/api/v2/runner/bootstrap` + `/register` + `/heartbeat` + `/events` **vẫn dùng HTTP** qua `BACKEND_URL=http://100.64.0.1:8001` (request ngắn, OK)
- CHỈ command claim/dispatch chuyển sang Redis BRPOP:
  - Subscribe list: `mt5:runner:{RUNNER_ID}:commands`
  - Pop: `BRPOPLPUSH mt5:runner:{RUNNER_ID}:commands mt5:runner:{RUNNER_ID}:commands:processing 0`
  - Sau khi xử lý xong: `LREM` khỏi processing list
  - KHÔNG còn `POST /api/v2/runner/commands/claim` long-poll nữa

### Phase C — Restart + verify

```powershell
# Restart node_control
Restart-Service node-control  # hoặc cách bạn đang dùng

# Watch log
Get-Content C:\path\to\runner\logs\node_control.log -Wait -Tail 50
```

Smoke test từ Linux team (họ sẽ confirm):
```bash
# Linux team chạy:
docker compose exec redis redis-cli -a "<PASSWORD>" \
  LPUSH "mt5:runner:runner-win-test-01:commands" \
  '{"command_id":"smoke-001","command_type":"PLACE_ORDER","payload":{"smoke":true}}'

# Windows runner phải pop được trong <100ms.
```

### Phase D — TradingView fan-out (test scenario)

Sau khi runner sẵn sàng, Linux team sẽ:
1. INSERT subscription rows vào `tradingview_signal_subscriptions` (account_id, signal_id)
2. Gửi 1 alert test: `POST /api/v2/public/tradingview/broadcast` với `{alert_id, signal_id, action: BUY, symbol, default_volume}`
3. Backend sẽ fan-out trong ~50ms tới N runners
4. Mỗi runner pop ngay → worker đặt lệnh MT5

Windows team verify: với 100 user trên 5 runners, mỗi runner nhận ~20 commands trong 1 second → place orders gần như đồng thời.

## 3. Báo cáo Linux team sau khi xong

- [ ] Tất cả VPS chạy Tailscale, ping `100.64.0.1` OK
- [ ] node_control khởi động với `RUNNER_TRANSPORT=redis_queue`, không còn 502
- [ ] Smoke test pop được command từ Redis (Linux team gửi)
- [ ] Bot start được trên Mini App với account thật
- [ ] Có lỗi gì khác không — gửi log snippet 100 dòng

## 4. Rollback nếu fail

```env
# Revert .env:
BACKEND_URL=https://cntxlabs.vercel.app
RUNNER_TRANSPORT=http_poll
# (xóa REDIS_HOST/PORT/PASSWORD)
```

```powershell
# Tắt tailscale (giữ cài, không join):
& "C:\Program Files\Tailscale\tailscale.exe" down

Restart-Service node-control
```

Hệ thống quay về trạng thái cũ (vẫn 502 cho claim, nhưng heartbeat/events/register chạy qua Vercel rewrite).

## 5. Vấn đề phụ team Windows tự fix

Lỗi `KeyError: "Attempt to overwrite 'args' in LogRecord"` ở watchdog (xem [logs/system_watchdog.log](../logs/system_watchdog.log:11)) — do code Windows runner truyền `extra={"args": ...}` clobbing reserved key của Python `LogRecord`. Đổi tên field, vd. `extra={"call_args": ...}`. Không liên quan backend.

---PROMPT END---
