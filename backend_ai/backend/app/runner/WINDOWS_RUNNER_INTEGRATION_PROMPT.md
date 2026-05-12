# Prompt tích hợp Windows Runner

**Cách dùng:** Sao chép toàn bộ nội dung file này (hoặc từng mục) gửi cho team Windows / coding agent trong repo runner (`windowns-runner-mt5-user-v1`). Đây là **hợp đồng kỹ thuật** để implement runner Python production cho MT5.

**Tóm tắt:** Runner kết nối backend FastAPI (control-plane) qua HTTP có header `X-Backend-Api-Key`, **đọc lệnh từ Redis** (`RUNNER_TRANSPORT=redis_queue`), tự đăng nhập MT5, bật/tắt bot và báo **event** + **delivery** về backend. **Không** gọi API người dùng / Mini App — chỉ được gọi các đường dẫn `/api/v2/runner/*`.

**Không có HTTP poll lệnh:** control-plane **không** cung cấp endpoint để runner “kéo” / long-poll từng lệnh qua HTTP. **Mọi lệnh điều khiển bot** (`START_BOT`, `STOP_BOT`, …) chỉ đến runner qua **Redis list** `mt5:runner:{RUNNER_ID}:commands`. HTTP chỉ dùng cho **callback ngắn** (đăng ký, heartbeat, events, delivery, package, xác minh) — không thay thế Redis cho lệnh.

---

## Vai trò

Bạn là coding agent trên máy **Windows runner**. Cần implement runner Python chạy production cho MT5.

Backend Linux đã có sẵn:

- API control-plane: `BACKEND_BASE_URL`
- Xác thực nội bộ: header `X-Backend-Api-Key: BACKEND_API_KEY`
- Hàng đợi lệnh Redis theo từng runner
- Schema lệnh / sự kiện chung trong `runner/schemas` (repo monorepo Linux hoặc bản đồng bộ trên Windows)

**Cấm:** gọi API user / Mini App từ runner. Runner **chỉ** được gọi các endpoint `/api/v2/runner/*`.

---

## Biến môi trường bắt buộc

```env
BACKEND_BASE_URL=https://<backend-host>
BACKEND_API_KEY=<same-secret-as-backend>
REDIS_URL=redis://:<password>@<redis-host>:6379/0
RUNNER_TRANSPORT=redis_queue
RUNNER_ID=runner-win-01
RUNNER_LABEL=Windows MT5 Runner 01
MAX_SLOTS=10
MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
RUNNER_WORK_DIR=C:\spider-runner
BOT_TRADING_ROOT=C:\spider-runner\bot-trading
```

**Không** ghi log `BACKEND_API_KEY`, mật khẩu Redis, mật khẩu MT5.

`REDIS_URL` là **bắt buộc**: runner lấy lệnh qua Redis list gắn với `RUNNER_ID`.

---

## Luồng kết nối hiện tại

**Backend → Windows runner (lệnh thực thi):** chỉ qua **Redis queue**

- List lệnh: `mt5:runner:{RUNNER_ID}:commands`
- List xác minh tài khoản: `mt5:runner:{RUNNER_ID}:verification`
- Khi dequeue, dùng list **processing**:
  - `mt5:runner:{RUNNER_ID}:commands:processing`
  - `mt5:runner:{RUNNER_ID}:verification:processing`

Windows phải **kết nối được Redis** (mesh/VPN nội bộ hoặc chính sách mạng cho phép).

**Windows runner → Backend:** HTTP callback vào FastAPI (request ngắn), kèm header `X-Backend-Api-Key`. **Không** dùng chuỗi HTTP này để nhận lệnh — lệnh chỉ từ Redis như trên.

---

## Phụ thuộc cài trên Windows

Dùng **Python 3.11+**.

```powershell
pip install httpx redis pydantic psutil MetaTrader5
```

Nếu repo runner đã có package riêng thì dùng package đó, nhưng **vẫn phải** giữ đúng hợp đồng dưới đây.

---

## API control-plane

Base URL: `${BACKEND_BASE_URL}/api/v2`

Mọi request thêm:

```http
X-Backend-Api-Key: ${BACKEND_API_KEY}
Content-Type: application/json
```

### Đăng ký runner (register)

Trước khi `register`, runner **nên** gọi `bootstrap` để lấy contract mới nhất:

`GET /api/v2/runner/bootstrap?runner_id=runner-win-01`

Ví dụ response (rút gọn — thực tế có thể dài hơn):

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
    "recommended": "redis_queue",
    "supported": ["redis_queue"],
    "redis_queue": {
      "commands": "mt5:runner:runner-win-01:commands",
      "commands_processing": "mt5:runner:runner-win-01:commands:processing",
      "verification": "mt5:runner:runner-win-01:verification",
      "verification_processing": "mt5:runner:runner-win-01:verification:processing"
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
      "kill_mt5": false
    }
  }
}
```

Gọi khi runner khởi động và **gọi lại** khi catalog bot / slot thay đổi.

`POST /api/v2/runner/register`

```json
{
  "runner_id": "runner-win-01",
  "label": "Windows MT5 Runner 01",
  "host": "WIN-MT5-01",
  "status": "online",
  "supported_profiles": ["light", "normal", "heavy"],
  "capability_tags": ["windows", "mt5", "redis_queue"],
  "capabilities": {
    "os": "windows",
    "transport": "redis_queue",
    "supported_transports": ["redis_queue"],
    "mt5_recovery": true,
    "runtime_login_required": true,
    "stop_policy": "end_task"
  },
  "available_bots": ["gsalgovip"],
  "available_bot_names": ["gsalgovip"],
  "bot_catalog": {
    "source": "disk",
    "bots": [
      {
        "bot_id": "gsalgovip",
        "bot_code": "gsalgovip",
        "bot_name": "GsAlgo VIP",
        "version": "0.3.0",
        "runtime_language": "python",
        "entrypoint": "app.runner_impl:run",
        "profile_class": "normal",
        "strategy_tags": ["mt5", "xauusd", "signal", "tradingview_webhook"],
        "resource_hints": {"runtime": "windows_mt5", "lane": "mt5_runner", "requires_mt5": true},
        "risk_contract": {"requires_sl": true, "requires_tp": true, "max_orders": 20},
        "config_schema": "config/schema.json",
        "default_config_path": "config/default.json",
        "checksum": "<sha256-from-runner.bot_catalog>"
      }
    ]
  },
  "max_slots": 10,
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

### Heartbeat (nhịp tim)

Gọi mỗi **5–10 giây**. Nếu slot đang chạy bot thì gửi `account_id` / `deployment_id` hiện tại.

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
    "available_bots": ["gsalgovip"],
    "available_bot_names": ["gsalgovip"],
    "bot_catalog": {
      "source": "disk",
      "bots": [
        {
          "bot_id": "gsalgovip",
          "bot_code": "gsalgovip",
          "version": "0.3.0",
          "entrypoint": "app.runner_impl:run",
          "checksum": "<sha256-from-runner.bot_catalog>"
        }
      ]
    }
  }
}
```

### Tải gói deployment (package)

Dùng khi xử lý lệnh `START_BOT`.

`GET /api/v2/runner/deployments/{deployment_id}/package`

Ví dụ dạng response:

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
    "bot_code": "gsalgovip",
    "bot_name": "gsalgovip",
    "status": "starting",
    "desired_state": "running",
    "runner_id": "runner-win-01",
    "slot_id": "slot-01",
    "config": {}
  },
  "bot": {
    "bot_id": "gsalgovip",
    "bot_name": "gsalgovip",
    "runtime_entry": "app.runner_impl:run",
    "profile_class": "normal",
    "resource_hints": {
      "runner_id": "runner-win-01",
      "slot_id": "slot-01",
      "terminal_path": "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
    }
  }
}
```

### Trạng thái giao lệnh (delivery)

Gọi khi nhận lệnh từ Redis và khi hoàn tất (delivery + event nếu cần).

`POST /api/v2/runner/commands/{command_id}/delivery`

Giá trị `delivery_status` được phép (theo tuple nghiệp vụ backend): `queued`, `dispatched`, `acknowledged`, `failed`.

```json
{
  "runner_id": "runner-win-01",
  "slot_id": "slot-01",
  "delivery_status": "dispatched",
  "error_text": null,
  "payload": {
    "phase": "dequeued_by_windows_runner"
  }
}
```

### Gửi sự kiện (emit event)

`POST /api/v2/runner/events`

Các `event_type` quan trọng:

- `BOT_STARTED`
- `BOT_STOPPED`
- `COMMAND_REJECTED`
- `RUNTIME_LOG`
- `SLOT_STATE_CHANGED`
- `SLOT_DEGRADED`
- `SLOT_BROKEN`

**Khởi động thành công:**

```json
{
  "event_id": "uuid",
  "event_type": "BOT_STARTED",
  "account_id": 101,
  "deployment_id": 9001,
  "bot_id": "gsalgovip",
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

**Khởi động thất bại do thông tin đăng nhập:**

```json
{
  "event_id": "uuid",
  "event_type": "COMMAND_REJECTED",
  "account_id": 101,
  "deployment_id": 9001,
  "bot_id": "gsalgovip",
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

Dùng các `error_code` sau để backend nhận diện sai tài khoản / mật khẩu / server:

- `INVALID_CREDENTIALS`
- `INVALID_PASSWORD`
- `INVALID_SERVER`
- `ACCOUNT_NOT_FOUND`

**Dừng thành công:**

```json
{
  "event_id": "uuid",
  "event_type": "BOT_STOPPED",
  "account_id": 101,
  "deployment_id": 9001,
  "bot_id": "gsalgovip",
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

---

## Quy tắc dequeue / xác nhận Redis

1. Khi runner khởi động, **khôi phục** lệnh đang xử lý (inflight):
   - Chuyển toàn bộ phần tử từ `verification:processing` về `verification`
   - Chuyển toàn bộ phần tử từ `commands:processing` về `commands`
2. Dequeue bằng `BRPOPLPUSH source processing timeout`.
3. Sau khi pop lệnh từ queue, gọi delivery với trạng thái `dispatched`.
4. Xử lý xong và callback backend thành công thì `LREM processing 1 raw`.
5. Nếu lỗi tạm thời thì **requeue**: `LREM processing 1 raw`, sau đó `RPUSH source raw`.
6. Lệnh phải **idempotent** theo `command_id`: nếu đã xử lý thành công rồi thì chỉ emit lại event/delivery khi cần, **không** khởi động trùng MT5/bot.

---

## Schema một phần tử lệnh trong Redis

Phần tử trong `mt5:runner:{runner_id}:commands` là JSON:

```json
{
  "command_id": "cmd-id",
  "command_type": "START_BOT",
  "cmd_type": "start_bot",
  "requested_cmd_type": "start_bot",
  "account_id": 101,
  "profile_id": 101,
  "deployment_id": 9001,
  "bot_id": "gsalgovip",
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

---

## Hành vi bắt buộc với `START_BOT`

Khi gặp `command_type = START_BOT`:

1. **Kiểm tra** mục tiêu lệnh:
   - `runner_id` phải bằng biến môi trường `RUNNER_ID`
   - `slot_id` phải là slot cục bộ đang có
2. Gọi delivery `dispatched`.
3. Tải package: `GET /api/v2/runner/deployments/{deployment_id}/package`.
4. Lấy thông tin tài khoản: `server`, `login`, `password`.
5. Nếu payload có:
   - `runtime_login_required=true`
   - `credential_check_policy=login_before_start`
   - `mt5_recovery_policy=recover_or_launch`

   thì runner phải:

   - Khôi phục hoặc khởi chạy MT5 nếu chưa chạy
   - Đăng nhập MT5 bằng tài khoản trong package
   - Xác minh server / tài khoản khớp
   - **Chỉ** start bot sau khi đăng nhập **thành công**

6. Nếu đăng nhập **thất bại**:
   - Emit `COMMAND_REJECTED`
   - Payload có `reason`, `error_code`, `phase=mt5_login`
   - Delivery `failed`
   - Ack phần tử Redis
   - **Không** start bot

7. Nếu đăng nhập **thành công**:
   - Start process worker/bot cho deployment
   - Emit `BOT_STARTED`
   - Delivery `acknowledged`
   - Ack phần tử Redis

Backend sẽ đánh dấu tài khoản `connected` khi nhận `BOT_STARTED`.

---

## Hành vi bắt buộc với `STOP_BOT`

Khi gặp `command_type = STOP_BOT`, payload từ backend có thể tương tự:

```json
{
  "stop_policy": "end_task",
  "end_task": true,
  "kill_worker": true,
  "kill_mt5": false,
  "terminate_mt5": false,
  "release_terminal": true
}
```

Runner phải:

1. Gọi delivery `dispatched`.
2. Dừng process bot nếu còn sống.
3. Kill process worker của deployment.
4. Không kill terminal MT5 mặc định; chỉ kill nếu payload chủ động gửi `kill_mt5=true` hoặc `terminate_mt5=true`.
5. Đưa trạng thái slot cục bộ về `ready`.
6. Emit `BOT_STOPPED`.
7. Gọi delivery `acknowledged`.
8. Ack phần tử Redis.

Thao tác dừng phải **idempotent**: nếu process đã chết vẫn emit `BOT_STOPPED` và `acknowledged`.

---

## Hành vi `UPDATE_BOT_CONFIG`

Nếu runner hỗ trợ cập nhật cấu hình nóng:

1. Áp dụng config vào bot đang chạy.
2. Emit `RUNTIME_LOG` hoặc event domain nếu có quy ước.
3. Delivery `acknowledged`.

Nếu **không** hỗ trợ:

1. Emit `COMMAND_REJECTED` với:
   - `reason=unsupported_command`
   - `phase=update_bot_config`
2. Delivery `failed`.

Backend có thể fallback restart nếu hot-update bị từ chối / timeout.

---

## Hàng đợi xác minh (verification)

Queue `mt5:runner:{runner_id}:verification` là luồng xác minh legacy / độc lập. Vẫn nên implement để **tương thích**.

Một job mẫu:

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

Luồng:

1. Dequeue job từ Redis (`mt5:runner:{runner_id}:verification` → `:processing` giống lệnh bot).
2. Tải bundle tài khoản: `GET /api/v2/runner/accounts/{account_id}/bundle`.
3. Launch / recover MT5.
4. Đăng nhập và xác minh server / tài khoản.
5. Gửi kết quả: `POST /api/v2/runner/account-verifications/result`

**Thành công:**

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

**Thất bại thông tin đăng nhập:**

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

---

## Mô hình process cục bộ

Mỗi slot nên có state riêng:

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

Nên lưu state cục bộ ra file JSON trong `RUNNER_WORK_DIR` để khi **restart** runner có thể recover / kill process cũ.

---

## Quy tắc đăng nhập MT5

Implement hàm `ensure_mt5_logged_in(account, terminal_path, slot)`:

1. Nếu terminal bị treo / trùng thì kill đúng process của slot.
2. Khởi chạy terminal nếu chưa chạy.
3. Gọi `MetaTrader5.initialize(path=terminal_path)`.
4. Gọi `MetaTrader5.login(login=int(account["login"]), password=..., server=...)`.
5. Kiểm tra `account_info()`:
   - `account_info.login` khớp
   - server / tài khoản đã kết nối
6. Trả về OK hoặc mã lỗi rõ ràng.

**Không** start bot khi đăng nhập thất bại.

---

## Ánh xạ lỗi bắt buộc

Ánh xạ lỗi MT5 sang payload backend:

- Sai mật khẩu / đăng nhập: `reason=invalid_credentials`, `error_code=INVALID_CREDENTIALS`
- Sai server: `reason=invalid_server`, `error_code=INVALID_SERVER`
- Không tìm thấy tài khoản: `reason=account_not_found`, `error_code=ACCOUNT_NOT_FOUND`
- Không launch được terminal: `reason=mt5_launch_failed`
- `MetaTrader5.initialize` thất bại: `reason=mt5_initialize_failed`
- Worker bot không start: `reason=bot_worker_start_failed`
- Slot lỗi bootstrap: `reason=slot_bootstrap_failed:fatal_<detail>`

Lỗi credential **phải** dùng đúng các mã trên để backend đánh dấu tài khoản `verification_failed`.

---

## Tiêu chí hoàn thành (done)

Runner được coi là **đúng hợp đồng** khi:

1. Khởi động runner → backend thấy runner **online** và có **heartbeat**.
2. Mini App kết nối tài khoản + start bot → backend đẩy lệnh `START_BOT`.
3. Windows runner đọc queue, tải package, đăng nhập MT5.
4. Sai credential → backend nhận `COMMAND_REJECTED`, tài khoản thành `verification_failed`.
5. Đúng credential → runner start bot, backend nhận `BOT_STARTED`, tài khoản `connected`, deployment `running`.
6. User stop bot → runner dừng worker/bot, giữ MT5 terminal mặc định, backend nhận `BOT_STOPPED`, deployment `stopped`.
7. Restart Windows runner **không** tạo bot trùng; queue `processing` được khôi phục đúng.
