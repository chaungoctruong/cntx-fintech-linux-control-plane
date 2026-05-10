# Windows Runner Handoff: runner-win-01

Do not send backend-only secrets to Windows. Send only this runner env plus the matching API/Redis secrets through a private channel.

## 1. Windows `.env`

```env
APP_ENV=test

BACKEND_URL=http://<TEST_BACKEND_HOST>:8001
RUNNER_CONTROL_PLANE_URL=http://<TEST_BACKEND_HOST>:8001
BACKEND_RUNNER_API_PREFIX=/api/v2

# Copy from backend_ai/backend/.env. Must match backend runtime exactly.
BACKEND_API_KEY=<copy_BACKEND_API_KEY_from_backend_env>

RUNNER_ID=runner-win-01
NODE_ID=runner-win-01
MT5_RUNNER_ID=runner-win-01
RUNNER_TRANSPORT=redis_queue

RUNNER_MAX_SLOTS=10
MT5_RUNNER_SLOT_PREFIX=slot-

# Windows Phase 1 catalog-only handoff. This directory must contain:
# gsalgovip/bot_manifest.json
BOT_TRADING_ROOT=C:\spider-runner\bot-trading

# Redis credentials are stored on the Linux VPS at:
# /root/runner-win-01-redis.env
#
# Recommended secure mode: open an SSH tunnel from Windows first:
# ssh -p 24700 -N -L 6380:127.0.0.1:6380 root@<TEST_BACKEND_HOST>
#
# Then use the REDIS_URL from /root/runner-win-01-redis.env:
# redis://:<REDIS_PASSWORD>@127.0.0.1:6380/0
REDIS_URL=redis://:<REDIS_PASSWORD>@127.0.0.1:6380/0
BOT_COMMAND_QUEUE_REDIS_URL=redis://:<REDIS_PASSWORD>@127.0.0.1:6380/0

MT5_RUNNER_COMMAND_QUEUE=mt5:runner:runner-win-01:commands
MT5_RUNNER_VERIFICATION_QUEUE=mt5:runner:runner-win-01:verification
MT5_EXECUTION_COMMAND_STREAM=mt5:execution:commands
MT5_EXECUTION_EVENT_STREAM=mt5:execution:events
```

Do not send these to Windows: `POSTGRES_*`, `TELEGRAM_BOT_TOKEN`, `SYSTEM_BOT_TOKEN`, `GEMINI_API_KEY`, `APP_SECRET_KEY`, `BROKER_API_CTRADER_*`, `AI_*`, `CTRADER_*`.

## 2. Backend Endpoints

Reachability:

```text
GET http://<TEST_BACKEND_HOST>:8001/health
GET http://<TEST_BACKEND_HOST>:8001/ready
```

Runner API:

```text
GET  /api/v2/runner/bootstrap?runner_id=runner-win-01
POST /api/v2/runner/register
POST /api/v2/runner/heartbeat
POST /api/v2/runner/events
GET  /api/v2/runner/commands/{command_id}
POST /api/v2/runner/commands/{command_id}/delivery
POST /api/v2/runner/commands/claim
GET  /api/v2/runner/accounts/{account_id}/bundle
GET  /api/v2/runner/deployments/{deployment_id}/package
POST /api/v2/runner/account-verifications/result
```

Every runner API request must include:

```text
X-Backend-Api-Key: <BACKEND_API_KEY>
```

## 3. PowerShell Smoke Tests

```powershell
$Backend = "http://<TEST_BACKEND_HOST>:8001"
$ApiKey = "<copy_BACKEND_API_KEY_from_backend_env>"
$Headers = @{ "X-Backend-Api-Key" = $ApiKey }

curl.exe "$Backend/health"
curl.exe "$Backend/ready"
curl.exe -H "X-Backend-Api-Key: $ApiKey" "$Backend/api/v2/runner/bootstrap?runner_id=runner-win-01"

# From the Windows runner repo/root after syncing bot-trading/gsalgovip:
python -m runner.bot_catalog --root "$env:BOT_TRADING_ROOT" --expect-bot gsalgovip --expect-version 0.3.0
```

If bootstrap returns `401 invalid_backend_api_key`, the key in Windows does not match the backend process currently running, or backend has not been restarted after changing `.env`.

## 4. Register runner-win-01

Register `max_slots=10` and use slot IDs `slot-01` through `slot-10`. Keep the same slot ID format in heartbeat, claim, delivery, and events.

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
  capability_tags = @("windows", "mt5", "http_poll", "redis_queue")
  capabilities = @{
    os = "windows"
    transport = "redis_queue"
    supported_transports = @("http_poll", "redis_queue")
    mt5_recovery = $true
    runtime_login_required = $true
    stop_policy = "end_task"
    max_slots = 10
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
  max_slots = 10
  slots = $Slots
} | ConvertTo-Json -Depth 8

Invoke-RestMethod -Method Post -Uri "$Backend/api/v2/runner/register" -Headers $Headers -Body $Body
```

## 5. Runtime Loop Contract

Recommended transport is `redis_queue`:

```text
mt5:runner:runner-win-01:commands
```

Use processing queue:

```text
mt5:runner:runner-win-01:commands:processing
```

HTTP polling remains available only as fallback:

```json
{
  "runner_id": "runner-win-01",
  "slot_id": "slot-01",
  "command_types": ["STOP_BOT", "START_BOT", "UPDATE_BOT_CONFIG"],
  "wait_timeout_sec": 10
}
```

Backend also writes stream:

```text
mt5:execution:commands
```

Runner events should be sent to:

```text
POST /api/v2/runner/events
```

Important event types: `BOT_STARTED`, `BOT_STOPPED`, `COMMAND_REJECTED`, `RUNTIME_LOG`, `SLOT_STATE_CHANGED`, `SLOT_DEGRADED`, `SLOT_BROKEN`.

For command completion, call:

```text
POST /api/v2/runner/commands/{command_id}/delivery
```

Allowed `delivery_status`: `queued`, `dispatched`, `acknowledged`, `failed`.
