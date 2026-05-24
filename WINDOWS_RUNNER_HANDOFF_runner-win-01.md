# Bàn giao Windows Runner: runner-win-01

**Mục đích:** hướng dẫn cấu hình **một node runner Windows** (`runner-win-01`): file `.env` trên Windows, khóa API khớp backend, Redis (thường qua SSH tunnel), kiểm tra `curl` tới `/health`, `/ready` và đăng ký runner.

**An toàn:** không gửi secret chỉ có trên backend (Postgres, token Telegram, khóa AI, v.v.) sang Windows. Chỉ gửi **env runner** + khóa API/Redis **khớp** backend qua **kênh riêng** (không chat công khai).

**Lệnh điều khiển bot:** chỉ qua **Redis** (`RUNNER_TRANSPORT=redis_queue`). **Không** có HTTP poll / long-poll để “lấy lệnh” từ control-plane; các `GET`/`POST` runner chỉ là callback ngắn (bootstrap, register, heartbeat, events, delivery, package).

Tài liệu liên quan: [WINDOWS_RUNNER_INTEGRATION_PROMPT.md](backend_ai/backend/app/runner/WINDOWS_RUNNER_INTEGRATION_PROMPT.md), [docs/WINDOWS_RUNNER_HANDOFF_PROMPT.md](docs/WINDOWS_RUNNER_HANDOFF_PROMPT.md).

---

## 1. File `.env` trên Windows

```env
APP_ENV=test

BACKEND_URL=http://<TEST_BACKEND_HOST>:8001
RUNNER_CONTROL_PLANE_URL=http://<TEST_BACKEND_HOST>:8001
BACKEND_RUNNER_API_PREFIX=/api/v2

# Sao chép từ backend_ai/backend/.env — phải khớp 100% với backend đang chạy.
BACKEND_API_KEY=<copy_BACKEND_API_KEY_from_backend_env>

RUNNER_ID=runner-win-01
NODE_ID=runner-win-01
MT5_RUNNER_ID=runner-win-01
RUNNER_TRANSPORT=redis_queue

RUNNER_MAX_SLOTS=12
MT5_RUNNER_SLOT_PREFIX=slot-

# Handoff catalog Windows phase 1 — thư mục phải có package, ví dụ:
# gsalgovip/bot_manifest.json
BOT_TRADING_ROOT=C:\spider-runner\bot-trading

# Thông tin Redis thường lưu trên VPS Linux tại (ví dụ):
# /root/runner-win-01-redis.env
#
# Khuyến nghị: mở SSH tunnel từ Windows trước:
# ssh -p 24700 -N -L 6380:127.0.0.1:6380 root@<TEST_BACKEND_HOST>
#
# Sau đó dùng REDIS_URL lấy từ file trên Linux, dạng map sang local:
# redis://:<REDIS_PASSWORD>@127.0.0.1:6380/0
REDIS_URL=redis://:<REDIS_PASSWORD>@127.0.0.1:6380/0
BOT_COMMAND_QUEUE_REDIS_URL=redis://:<REDIS_PASSWORD>@127.0.0.1:6380/0

MT5_RUNNER_COMMAND_QUEUE=mt5:runner:runner-win-01:commands
MT5_RUNNER_VERIFICATION_QUEUE=mt5:runner:runner-win-01:verification
MT5_EXECUTION_COMMAND_STREAM=mt5:execution:commands
MT5_EXECUTION_EVENT_STREAM=mt5:execution:events
```

**Không** gửi sang Windows các biến chỉ dùng backend: `POSTGRES_*`, `TELEGRAM_BOT_TOKEN`, `SYSTEM_BOT_TOKEN`, `GEMINI_API_KEY`, `APP_SECRET_KEY`, `BROKER_API_CTRADER_*`, `AI_*`, `CTRADER_*`, v.v.

---

## 2. Endpoint backend (tham chiếu)

**Kiểm tra tới được máy chủ:**

```text
GET http://<TEST_BACKEND_HOST>:8001/health
GET http://<TEST_BACKEND_HOST>:8001/ready
```

**API runner** (gọi kèm header `X-Backend-Api-Key`):

```text
GET  /api/v2/runner/bootstrap?runner_id=runner-win-01
POST /api/v2/runner/register
POST /api/v2/runner/heartbeat
POST /api/v2/runner/events
GET  /api/v2/runner/commands/{command_id}
POST /api/v2/runner/commands/{command_id}/delivery
GET  /api/v2/runner/accounts/{account_id}/bundle
GET  /api/v2/runner/deployments/{deployment_id}/package
POST /api/v2/runner/account-verifications/result
```

Mọi request runner phải có:

```text
X-Backend-Api-Key: <BACKEND_API_KEY>
```

---

## 3. Smoke test PowerShell

```powershell
$Backend = "http://<TEST_BACKEND_HOST>:8001"
$ApiKey = "<copy_BACKEND_API_KEY_from_backend_env>"
$Headers = @{ "X-Backend-Api-Key" = $ApiKey }

curl.exe "$Backend/health"
curl.exe "$Backend/ready"
curl.exe -H "X-Backend-Api-Key: $ApiKey" "$Backend/api/v2/runner/bootstrap?runner_id=runner-win-01"

# Từ repo runner Windows, sau khi đồng bộ bot-trading/gsalgovip (semver lấy đúng bot_manifest.json):
python -m runner.bot_catalog --root "$env:BOT_TRADING_ROOT" --expect-bot gsalgovip --expect-version <semver-trong-manifest>
```

Nếu `bootstrap` trả `401 invalid_backend_api_key`: khóa trên Windows **không khớp** process backend đang chạy, hoặc backend **chưa restart** sau khi đổi `.env`.

---

## 4. Đăng ký runner-win-01

Đăng ký `max_slots=12`, slot `slot-01` … `slot-12`. Giữ **cùng định dạng** `slot_id` trong heartbeat, dequeue Redis, delivery và events.

```powershell
$Backend = "http://<TEST_BACKEND_HOST>:8001"
$ApiKey = "<copy_BACKEND_API_KEY_from_backend_env>"
$Headers = @{
  "X-Backend-Api-Key" = $ApiKey
  "Content-Type" = "application/json"
}

$Slots = 1..10 | ForEach-Object {
  @{
    slot_id = ("slot-{0:D2}" -f $_)
    status = "ready"
    allowed_profile_classes = @("light", "normal", "heavy")
    metadata = @{
      storage_slot_id = ("slot-{0:D2}" -f $_)
      start_eligible = $true
      ipc_ready = $true
    }
  }
}

$Body = @{
  runner_id = "runner-win-01"
  label = "Windows MT5 Runner 01"
  host = $env:COMPUTERNAME
  status = "online"
  supported_profiles = @("light", "normal", "heavy")
  capability_tags = @("windows", "mt5", "redis_queue")
  capabilities = @{
    os = "windows"
    transport = "redis_queue"
    supported_transports = @("redis_queue")
    mt5_recovery = $true
    runtime_login_required = $true
    stop_policy = "end_task"
    max_slots = 12
  }
  available_bots = @("gsalgovip")
  available_bot_names = @("gsalgovip")
  bot_catalog = @{
    source = "disk"
    bots = @(
      @{
        bot_id = "gsalgovip"
        bot_code = "gsalgovip"
        bot_name = "GsAlgo VIP"
        version = "0.3.0"
        runtime_language = "python"
        entrypoint = "app.runner_impl:run"
        profile_class = "normal"
        strategy_tags = @("mt5", "xauusd", "signal", "tradingview_webhook")
        resource_hints = @{ runtime = "windows_mt5"; lane = "mt5_runner"; requires_mt5 = $true }
        risk_contract = @{ requires_sl = $true; requires_tp = $true; max_orders = 20 }
        config_schema = "config/schema.json"
        default_config_path = "config/default.json"
        checksum = "<sha256-from-runner.bot_catalog>"
      }
    )
  }
  max_slots = 12
  slots = $Slots
} | ConvertTo-Json -Depth 8

Invoke-RestMethod -Method Post -Uri "$Backend/api/v2/runner/register" -Headers $Headers -Body $Body
```

*(Trường `version` / `checksum` trong `bot_catalog` phải khớp output lệnh `runner.bot_catalog` trên máy Windows — không copy cứng nếu manifest đã đổi.)*

---

## 5. Hợp đồng vòng lặp runtime

**Transport khuyến nghị:** `redis_queue` — hàng đợi lệnh:

```text
mt5:runner:runner-win-01:commands
```

Hàng xử lý (processing):

```text
mt5:runner:runner-win-01:commands:processing
```

Backend còn ghi stream (audit / pipeline):

```text
mt5:execution:commands
```

**Gửi event** về backend:

```text
POST /api/v2/runner/events
```

Một số `event_type` quan trọng: `BOT_STARTED`, `BOT_STOPPED`, `COMMAND_REJECTED`, `RUNTIME_LOG`, `SLOT_STATE_CHANGED`, `SLOT_DEGRADED`, `SLOT_BROKEN`.

**Báo cáo kết quả giao lệnh** (delivery):

```text
POST /api/v2/runner/commands/{command_id}/delivery
```

Giá trị `delivery_status` được phép (tuple nghiệp vụ): `queued`, `dispatched`, `acknowledged`, `failed`.
