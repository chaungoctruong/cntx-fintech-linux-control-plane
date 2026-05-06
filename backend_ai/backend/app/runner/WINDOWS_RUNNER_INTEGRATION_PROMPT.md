# Windows Runner Integration Prompt

Copy prompt nay sang ben Windows runner/coding agent. Muc tieu la Windows runner noi vao backend FastAPI lam control-plane callback, doc Redis queue, tu dang nhap MT5, bat/tat bot va bao ket qua ve backend.

## Vai tro

Ban la coding agent tren may Windows runner. Hay implement runner Python chay production cho MT5.

Backend da co san:
- Control-plane API: `BACKEND_BASE_URL`
- Auth noi bo: header `X-Backend-Api-Key: BACKEND_API_KEY`
- Redis command queue per runner
- Schema command/event chung trong `runner/schemas`

Khong goi API user/Mini App tu runner. Runner chi duoc goi cac endpoint `/api/v2/runner/*`.

## Env bat buoc

```env
BACKEND_BASE_URL=https://<backend-host>
BACKEND_API_KEY=<same-secret-as-backend>
REDIS_URL=redis://:<password>@<redis-host>:6379/0
RUNNER_ID=runner-win-01
RUNNER_LABEL=Windows MT5 Runner 01
MAX_SLOTS=1
MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
RUNNER_WORK_DIR=C:\spider-runner
```

Khong log `BACKEND_API_KEY`, Redis password, MT5 password.

`REDIS_URL` chi bat buoc neu runner chon transport `redis_queue`. Neu chon `http_poll`, runner chi can outbound HTTPS den backend.

## Ket noi hien tai

Backend -> Windows runner co 2 transport hop le:
- Product/fallback API mode: `POST /api/v2/runner/commands/claim`
- Redis queue mode:
  - Redis list: `mt5:runner:{RUNNER_ID}:commands`
  - Redis list: `mt5:runner:{RUNNER_ID}:verification`
  - Khi claim thi dung processing list:
    - `mt5:runner:{RUNNER_ID}:commands:processing`
    - `mt5:runner:{RUNNER_ID}:verification:processing`

Neu Windows khong noi Redis truc tiep duoc, dung `http_poll`. Neu da dung `http_poll` cho mot `RUNNER_ID`, khong chay them Redis consumer cho cung runner do.

Windows runner -> Backend:
- HTTP callback vao FastAPI, header `X-Backend-Api-Key`.

## Dependencies de cai tren Windows

Dung Python 3.11+.

```powershell
pip install httpx redis pydantic psutil MetaTrader5
```

Neu runner repo co san package rieng thi dung package do, nhung van phai giu dung contract ben duoi.

## API control-plane

Base URL: `${BACKEND_BASE_URL}/api/v2`

Tat ca request them:

```http
X-Backend-Api-Key: ${BACKEND_API_KEY}
Content-Type: application/json
```

### Register runner

Truoc khi register, runner nen goi bootstrap de lay contract moi nhat:

`GET /api/v2/runner/bootstrap?runner_id=runner-win-01`

Response mau:

```json
{
  "server_time": "2026-05-05T00:00:00Z",
  "runner_id": "runner-win-01",
  "control_plane": {
    "base_url": "https://backend.example",
    "api_base": "https://backend.example/api/v2",
    "auth_header": "X-Backend-Api-Key"
  },
  "transport": {
    "recommended": "http_poll",
    "supported": ["http_poll", "redis_queue"],
    "http_poll": {
      "claim_path": "/api/v2/runner/commands/claim",
      "wait_timeout_sec": 10,
      "idle_poll_sec": 1,
      "claim_lease_sec": 180,
      "command_types": ["STOP_BOT", "START_BOT", "UPDATE_BOT_CONFIG"]
    }
  },
  "contract": {
    "start_bot": {
      "runtime_login_required": true,
      "credential_check_policy": "login_before_start",
      "mt5_recovery_policy": "recover_or_launch"
    },
    "stop_bot": {
      "stop_policy": "end_task",
      "kill_worker": true,
      "kill_mt5": true
    }
  }
}
```

Call khi runner start va call lai khi bot catalog/slot thay doi.

`POST /api/v2/runner/register`

```json
{
  "runner_id": "runner-win-01",
  "label": "Windows MT5 Runner 01",
  "host": "WIN-MT5-01",
  "status": "online",
  "supported_profiles": ["light", "normal", "heavy"],
  "capability_tags": ["windows", "mt5", "redis_queue", "http_callback"],
  "capabilities": {
    "os": "windows",
    "transport": "http_poll",
    "supported_transports": ["http_poll", "redis_queue"],
    "mt5_recovery": true,
    "runtime_login_required": true,
    "stop_policy": "end_task"
  },
  "available_bots": ["gsalgo_mt5_bot"],
  "available_bot_names": ["gsalgo_mt5_bot"],
  "bot_catalog": {},
  "max_slots": 1,
  "slots": [
    {
      "slot_id": "slot-01",
      "status": "ready",
      "allowed_profile_classes": ["light", "normal", "heavy"],
      "metadata": {
        "terminal_path": "C:\\Program Files\\MetaTrader 5\\terminal64.exe",
        "storage_slot_id": "slot-01",
        "start_eligible": true,
        "ipc_ready": true
      }
    }
  ]
}
```

### Heartbeat

Call moi 5-10 giay. Neu slot dang chay bot thi gui account/deployment hien tai.

`POST /api/v2/runner/heartbeat`

```json
{
  "runner_id": "runner-win-01",
  "slot_id": "slot-01",
  "account_id": 101,
  "deployment_id": 9001,
  "trace_id": "trace-id",
  "payload": {
    "slot_state": "running",
    "terminal_pid": 1234,
    "worker_pid": 5678,
    "mt5_connected": true,
    "available_bots": ["gsalgo_mt5_bot"]
  }
}
```

### Fetch deployment package

Dung khi xu ly `START_BOT`.

`GET /api/v2/runner/deployments/{deployment_id}/package`

Response co dang:

```json
{
  "deployment_id": 9001,
  "account_id": 101,
  "trace_id": "trace-id",
  "account": {
    "account_id": 101,
    "broker": "Broker",
    "server": "Broker-Server",
    "login": "123456",
    "password": "plain-password",
    "status": "pending_verification"
  },
  "binding": {
    "runner_id": "runner-win-01",
    "slot_id": "slot-01"
  },
  "deployment": {
    "deployment_id": 9001,
    "bot_code": "gsalgo_mt5_bot",
    "bot_name": "gsalgo_mt5_bot",
    "status": "starting",
    "desired_state": "running",
    "runner_id": "runner-win-01",
    "slot_id": "slot-01",
    "config": {}
  },
  "bot": {
    "bot_id": "gsalgo_mt5_bot",
    "bot_name": "gsalgo_mt5_bot",
    "runtime_entry": "main.py",
    "profile_class": "normal",
    "resource_hints": {
      "runner_id": "runner-win-01",
      "slot_id": "slot-01",
      "terminal_path": "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
    }
  }
}
```

### Delivery status

Call khi claim command va khi hoan tat. Neu dung `POST /runner/commands/claim`, backend da tu mark `dispatched`, runner khong can call `dispatched` lan nua.

`POST /api/v2/runner/commands/{command_id}/delivery`

Allowed `delivery_status`: `queued`, `dispatched`, `acknowledged`, `failed`.

```json
{
  "runner_id": "runner-win-01",
  "slot_id": "slot-01",
  "delivery_status": "dispatched",
  "error_text": null,
  "payload": {
    "phase": "claimed_by_windows_runner"
  }
}
```

### HTTP claim command

Dung endpoint nay neu Windows runner khong doc Redis truc tiep, hoac muon chi di qua HTTPS API.

`POST /api/v2/runner/commands/claim`

Request:

```json
{
  "runner_id": "runner-win-01",
  "slot_id": "slot-01",
  "command_types": ["STOP_BOT", "START_BOT", "UPDATE_BOT_CONFIG"],
  "wait_timeout_sec": 10
}
```

Response khi co command:

```json
{
  "empty": false,
  "runner_id": "runner-win-01",
  "slot_id": "slot-01",
  "command_id": "cmd-id",
  "delivery_status": "dispatched",
  "delivery_transport": "http_poll",
  "claim_lease_sec": 180,
  "lease_expires_at_epoch": 1770000180,
  "requeued_expired_claims": 0,
  "next_poll_sec": 0,
  "redis_cleanup": {"removed": 1},
  "command": {
    "command_id": "cmd-id",
    "command_type": "START_BOT",
    "cmd_type": "start_bot",
    "requested_cmd_type": "start_bot",
    "account_id": 101,
    "profile_id": 101,
    "deployment_id": 9001,
    "bot_id": "gsalgo_mt5_bot",
    "runner_id": "runner-win-01",
    "slot_id": "slot-01",
    "priority": 50,
    "payload": {
      "runtime_login_required": true,
      "credential_check_policy": "login_before_start",
      "mt5_recovery_policy": "recover_or_launch"
    },
    "created_at": "2026-05-05T00:00:00Z",
    "trace_id": "trace-id"
  }
}
```

Response khi khong co command:

```json
{
  "empty": true,
  "command": null,
  "runner_id": "runner-win-01",
  "slot_id": "slot-01",
  "delivery_transport": "http_poll",
  "next_poll_sec": 1,
  "claim_lease_sec": 180,
  "requeued_expired_claims": 0
}
```

### Emit event

`POST /api/v2/runner/events`

Event types quan trong:
- `BOT_STARTED`
- `BOT_STOPPED`
- `COMMAND_REJECTED`
- `RUNTIME_LOG`
- `SLOT_STATE_CHANGED`
- `SLOT_DEGRADED`
- `SLOT_BROKEN`

Start thanh cong:

```json
{
  "event_id": "uuid",
  "event_type": "BOT_STARTED",
  "account_id": 101,
  "deployment_id": 9001,
  "bot_id": "gsalgo_mt5_bot",
  "runner_id": "runner-win-01",
  "slot_id": "slot-01",
  "severity": "info",
  "command_id": "cmd-id",
  "trace_id": "trace-id",
  "payload": {
    "login_ok": true,
    "terminal_pid": 1234,
    "worker_pid": 5678
  }
}
```

Start that bai vi credential:

```json
{
  "event_id": "uuid",
  "event_type": "COMMAND_REJECTED",
  "account_id": 101,
  "deployment_id": 9001,
  "bot_id": "gsalgo_mt5_bot",
  "runner_id": "runner-win-01",
  "slot_id": "slot-01",
  "severity": "error",
  "command_id": "cmd-id",
  "trace_id": "trace-id",
  "payload": {
    "reason": "invalid_credentials",
    "error_code": "INVALID_CREDENTIALS",
    "phase": "mt5_login"
  }
}
```

Dung cac `error_code` nay de backend nhan dien sai account/password/server:
- `INVALID_CREDENTIALS`
- `INVALID_PASSWORD`
- `INVALID_SERVER`
- `ACCOUNT_NOT_FOUND`

Stop thanh cong:

```json
{
  "event_id": "uuid",
  "event_type": "BOT_STOPPED",
  "account_id": 101,
  "deployment_id": 9001,
  "bot_id": "gsalgo_mt5_bot",
  "runner_id": "runner-win-01",
  "slot_id": "slot-01",
  "severity": "info",
  "command_id": "cmd-id",
  "trace_id": "trace-id",
  "payload": {
    "reason": "user_stop_request",
    "worker_killed": true,
    "mt5_killed": true
  }
}
```

## Transport claim/ack rules

### HTTP poll mode

1. Loop call `POST /api/v2/runner/commands/claim` voi `wait_timeout_sec` 5-10.
2. Neu `empty=true`, tiep tuc heartbeat va poll lai.
3. Neu co `command`, xu ly command do.
4. Khi command thanh cong, emit event domain (`BOT_STARTED`, `BOT_STOPPED`, ...) roi call delivery `acknowledged`.
5. Khi command loi product/credential, emit `COMMAND_REJECTED` roi call delivery `failed`.
6. Neu loi tam thoi va muon retry, call delivery `queued` de command quay lai hang doi HTTP claim.
7. Phai hoan tat truoc `lease_expires_at_epoch`; neu runner chet/qua han, backend se tu requeue command cho lan claim sau.
8. Command phai idempotent theo `command_id`.

### Redis queue mode

1. Khi runner start, recover inflight:
   - move het item tu `verification:processing` ve `verification`
   - move het item tu `commands:processing` ve `commands`
2. Claim bang `BRPOPLPUSH source processing timeout`.
3. Sau khi claim command, call delivery `dispatched`.
4. Xu ly xong va callback backend thanh cong thi `LREM processing 1 raw`.
5. Neu loi tam thoi thi requeue: `LREM processing 1 raw`, sau do `RPUSH source raw`.
6. Command phai idempotent theo `command_id`: neu da xu ly thanh cong roi thi chi emit lai event/delivery neu can, khong start duplicate MT5/bot.

## Command schema

Item trong `mt5:runner:{runner_id}:commands` hoac response `command` cua HTTP claim la JSON:

```json
{
  "command_id": "cmd-id",
  "command_type": "START_BOT",
  "cmd_type": "start_bot",
  "requested_cmd_type": "start_bot",
  "account_id": 101,
  "profile_id": 101,
  "deployment_id": 9001,
  "bot_id": "gsalgo_mt5_bot",
  "runner_id": "runner-win-01",
  "slot_id": "slot-01",
  "priority": 50,
  "payload": {
    "command_type": "START_BOT",
    "cmd_type": "start_bot",
    "requested_cmd_type": "start_bot",
    "account_id": 101,
    "profile_id": 101,
    "deployment_id": 9001,
    "runner_id": "runner-win-01",
    "slot_id": "slot-01",
    "runtime_login_required": true,
    "credential_check_policy": "login_before_start",
    "mt5_recovery_policy": "recover_or_launch"
  },
  "created_at": "2026-05-05T00:00:00Z",
  "trace_id": "trace-id"
}
```

## START_BOT behavior bat buoc

Khi gap `command_type = START_BOT`:

1. Validate command target:
   - `runner_id` phai bang env `RUNNER_ID`
   - `slot_id` phai la slot local co san
2. Call delivery `dispatched`.
3. Fetch package: `GET /api/v2/runner/deployments/{deployment_id}/package`.
4. Lay account:
   - `server`
   - `login`
   - `password`
5. Neu payload co:
   - `runtime_login_required=true`
   - `credential_check_policy=login_before_start`
   - `mt5_recovery_policy=recover_or_launch`

   thi runner phai:
   - recover hoac launch MT5 neu chua chay
   - login MT5 bang account trong package
   - verify account login/server match
   - chi start bot sau khi login OK
6. Neu login fail:
   - emit `COMMAND_REJECTED`
   - payload co `reason`, `error_code`, `phase=mt5_login`
   - delivery `failed`
   - ack Redis item
   - khong start bot
7. Neu login OK:
   - start worker/bot process cho deployment
   - emit `BOT_STARTED`
   - delivery `acknowledged`
   - ack Redis item

Backend se mark account `connected` khi nhan `BOT_STARTED`.

## STOP_BOT behavior bat buoc

Khi gap `command_type = STOP_BOT`, payload backend da set:

```json
{
  "stop_policy": "end_task",
  "end_task": true,
  "kill_worker": true,
  "kill_mt5": true,
  "terminate_mt5": true,
  "release_terminal": true
}
```

Runner phai:
1. Call delivery `dispatched`.
2. Stop bot process neu con song.
3. Kill worker process cua deployment.
4. Kill MT5 terminal cua slot neu `kill_mt5=true` hoac `terminate_mt5=true`.
5. Release local slot state ve `ready`.
6. Emit `BOT_STOPPED`.
7. Call delivery `acknowledged`.
8. Ack Redis item.

Stop phai idempotent: neu process da chet thi van emit `BOT_STOPPED` va acknowledged.

## UPDATE_BOT_CONFIG behavior

Neu runner support update nong config:
1. Apply config vao bot dang chay.
2. Emit `RUNTIME_LOG` hoac event domain neu co.
3. Delivery `acknowledged`.

Neu khong support:
1. Emit `COMMAND_REJECTED` voi:
   - `reason=unsupported_command`
   - `phase=update_bot_config`
2. Delivery `failed`.

Backend co fallback restart neu hot-update bi reject/timeout.

## Verification queue behavior

Queue `mt5:runner:{runner_id}:verification` la legacy/standalone verification. Van implement de tuong thich.

Item:

```json
{
  "job_id": 1,
  "account_id": 101,
  "runner_id": "runner-win-01",
  "slot_id": "slot-01",
  "trace_id": "trace-id",
  "payload": {}
}
```

Flow:
1. Claim verification.
2. Fetch account bundle: `GET /api/v2/runner/accounts/{account_id}/bundle`.
3. Launch/recover MT5.
4. Login and verify account/server.
5. Submit result:

`POST /api/v2/runner/account-verifications/result`

```json
{
  "job_id": 1,
  "ok": true,
  "runner_id": "runner-win-01",
  "slot_id": "slot-01",
  "error_text": null,
  "payload": {
    "phase": "mt5_login",
    "login_ok": true
  }
}
```

Fail credential:

```json
{
  "job_id": 1,
  "ok": false,
  "runner_id": "runner-win-01",
  "slot_id": "slot-01",
  "error_text": "invalid_credentials",
  "payload": {
    "error_code": "INVALID_CREDENTIALS",
    "phase": "mt5_login",
    "retryable": false
  }
}
```

## Local process model

Moi slot nen co state rieng:

```json
{
  "slot_id": "slot-01",
  "terminal_path": "C:\\Program Files\\MetaTrader 5\\terminal64.exe",
  "terminal_pid": null,
  "worker_pid": null,
  "account_id": null,
  "deployment_id": null,
  "command_id": null,
  "state": "ready"
}
```

Can luu state local vao file JSON trong `RUNNER_WORK_DIR` de restart runner co the recover/kill process cu.

## MT5 login rules

Implement ham `ensure_mt5_logged_in(account, terminal_path, slot)`:
1. Neu terminal stale/duplicate thi kill dung process cua slot.
2. Launch terminal neu chua chay.
3. Goi `MetaTrader5.initialize(path=terminal_path)`.
4. Goi `MetaTrader5.login(login=int(account["login"]), password=..., server=...)`.
5. Check `account_info()`:
   - account_info.login match
   - server/account connected
6. Return OK hoac error code ro rang.

Khong start bot khi login fail.

## Required error mapping

Map loi MT5 sang backend payload:

- Sai password/login: `reason=invalid_credentials`, `error_code=INVALID_CREDENTIALS`
- Sai server: `reason=invalid_server`, `error_code=INVALID_SERVER`
- Account khong ton tai: `reason=account_not_found`, `error_code=ACCOUNT_NOT_FOUND`
- Terminal khong launch duoc: `reason=mt5_launch_failed`
- MT5 initialize fail: `reason=mt5_initialize_failed`
- Bot worker fail: `reason=bot_worker_start_failed`
- Slot dang loi: `reason=slot_bootstrap_failed:fatal_<detail>`

Credential error phai dung code o tren de backend mark account `verification_failed`.

## Done criteria

Runner duoc xem la dung khi:
1. Start runner -> backend thay runner online va heartbeat.
2. Mini App connect account + start bot -> backend day `START_BOT`.
3. Windows runner doc queue, fetch package, login MT5.
4. Sai credential -> backend nhan `COMMAND_REJECTED`, account thanh `verification_failed`.
5. Dung credential -> runner start bot, backend nhan `BOT_STARTED`, account thanh `connected`, deployment `running`.
6. User stop bot -> runner kill worker + MT5, backend nhan `BOT_STOPPED`, deployment `stopped`.
7. Restart Windows runner khong lam duplicate bot; processing queue duoc recover.
