# GsAlgo VIP

`gsalgovip` là một **package catalog Linux** do control-plane Spider AI
MT5 SaaS bảo trì. Đây là bản clone đã được vệ sinh lại từ package
`bot-trading/gsalgo/` đã được kiểm chứng trên production. `gsalgo` gốc
chạy trên một Windows runner duy nhất cho một tài khoản MT5;
**`gsalgovip` là biến thể được Linux backend phân phối tới nhiều Windows
runner / nhiều tenant.**

> Package gốc `bot-trading/gsalgo/` KHÔNG bị sửa. Mọi thứ riêng cho một
> account/slot/path cụ thể chỉ tồn tại ở đó.

> **Tuân thủ:** `gsalgovip` v0.3.0 tuân theo
> [`bot-trading/PACKAGE_STANDARD.md`](../PACKAGE_STANDARD.md) v1.
>
> **Cần triển khai đa tenant?** Kế hoạch tích hợp chung
> ([`bot-trading/PLATFORM_INTEGRATION_PLAN.md`](../PLATFORM_INTEGRATION_PLAN.md))
> xử lý `gsalgovip` và mọi bot tương lai. Quyết định riêng cho bot này
> (port mặc định, dải magic, lệnh smoke) ở
> [`./INTEGRATION_NOTES.md`](./INTEGRATION_NOTES.md).



## Có gì mới ở 0.3.0

- **Mô hình triển khai đa tenant trở thành tiêu chuẩn first-class.**
  Package được triển khai theo dạng **một process OS / một tenant** trên
  Windows runner. Nhiều tenant trên một runner, nhiều runner trên cluster.
  Xem [`./INTEGRATION_NOTES.md`](./INTEGRATION_NOTES.md) và
  [`bot-trading/PLATFORM_INTEGRATION_PLAN.md`](../PLATFORM_INTEGRATION_PLAN.md)
  để biết contract đầy đủ.
- Env mới (tùy chọn, dùng cho log routing & metric): `TENANT_ID`,
  `INSTANCE_ID`.
- Env mới (tùy chọn, cho upstream nginx dùng chung): `WEBHOOK_PATH` —
  nếu platform serve đa tenant qua path prefix kiểu
  `/t/<tenant_id>/webhook/tradingview`.
- `/healthz` giờ trả `tenant_id` và `instance_id`.
- Dòng log giờ có prefix `[<tenant_id>@<instance_id>]` để aggregator
  chung tách stream per-tenant không phải đụng log level.
- Manifest có thêm khối `deployment_model` mô tả contract phân phối mà
  platform phải đáp ứng.

## Có gì mới ở 0.2.0

- **State store: PostgreSQL** (trước là SQLite ở 0.1.0).
- Bot giờ yêu cầu `DATABASE_URL` được inject bởi runtime context của
  platform. DB thuộc về bot, KHÔNG thuộc Linux backend core.
- Nâng cấp concurrency: `claim_pending_signal()` dùng `FOR UPDATE SKIP
  LOCKED` để nhiều worker dùng chung DB an toàn.
- Schema được auto-create khi startup; `db/schema.sql` cũng có sẵn nếu
  platform muốn pre-provision tables.

## Vai trò các plane

| Plane | Vai trò |
|---|---|
| Linux backend (repo này) | Catalog, đăng ký, validate, cấu hình, phân phối, gate risk, observe |
| Windows runner | Thực thi package với MT5 terminal thật |
| PG riêng của bot | Lưu `signals` và `executions`. **Tách rời** với Linux core DB. |

Phía Linux đọc `bot_manifest.json`, `config/schema.json` và
`config/default.json` để đăng ký `gsalgovip` vào catalog. Bản thân
runtime (FastAPI webhook + worker + MT5 executor) chỉ chạy trên Windows.

## Tóm tắt manifest

- `bot_id`: `gsalgovip`
- `version`: xem `VERSION` (hiện `0.3.0`)
- `runtime_language`: `python`
- `entrypoint`: `app.runner_impl:run` (stub an toàn, không trade)
- Runtime thật (do platform quản lý trên Windows):
  - FastAPI ASGI: `app.main:app`
  - Worker: `app.run_worker:main`
- `profile_class`: `normal`
- `strategy_tags`: `mt5`, `xauusd`, `signal`, `gsalgo`, `tradingview_webhook`,
  `postgres_state`
- `data_store.kind`: `postgresql`
- `data_store.must_be_separate_from_linux_core_db`: `true`
- `resource_hints.runtime`: `windows_mt5`, `requires_postgres`: `true`
- `risk_contract`: yêu cầu SL+TP, basket đơn, max 20 lệnh, có rate-limit

## Env bắt buộc (do platform inject)

| Var | Mục đích | Ví dụ (KHÔNG BAO GIỜ commit giá trị thật) |
|---|---|---|
| `DATABASE_URL` | PG riêng của bot | `postgresql://gsalgovip_t1:***@db-host:5432/gsalgovip_t1` |
| `WEBHOOK_SECRET` | Secret chia sẻ với TradingView | `***` |
| `MT5_PASSWORD` | Mật khẩu MT5 (runtime Windows) | `***` |
| `MT5_LOGIN` | Số tài khoản MT5 | riêng theo tenant |
| `MT5_SERVER` | Server broker | riêng theo tenant |
| `MT5_TERMINAL_PATH` | Đường dẫn MT5 terminal exe | runner Windows tự inject |
| `MT5_MAGIC` | Magic number | riêng theo tenant |
| `TELEGRAM_BOT_TOKEN` *(opt)* | Cho thông báo | `***` |
| `TELEGRAM_CHAT_ID` *(opt)* | Cho thông báo | `***` |
| `TENANT_ID` | ID per-tenant (chỉ để log) | `t42` |
| `INSTANCE_ID` | ID per-process (chỉ để log) | `t42-runner-w2-slot-3` |
| `WEBHOOK_PATH` *(opt)* | Override path mount webhook | `/t/t42/webhook/tradingview` |
| `APP_PORT` *(opt)* | Port bind (platform cấp duy nhất per-tenant) | `8042` |

> Bản thân package **không bao giờ đọc credential từ disk**. `.env.example`
> trong repo này chỉ chứa placeholder rỗng.

## Package này có gì

- `bot_manifest.json` — descriptor catalog (không có secret)
- `VERSION` — phiên bản semver (khớp manifest)
- `config/schema.json` — JSON Schema 2020-12 validate config tenant
- `config/default.json` — config default an toàn (dry-run, lot 0.01,
  không có account thật)
- `db/schema.sql` — DDL tường minh (idempotent; khớp với cái
  `state_store` auto-run)
- `README.md` — file này
- `requirements.txt` — dep Python (`fastapi`, `uvicorn`, `pydantic`,
  `httpx`, `psycopg[binary]>=3.1`)
- `.env.example` — env placeholder, KHÔNG có secret, KHÔNG có path
  production
- `.gitignore` — loại trừ runtime data, log, secret
- `app/` — source Python:
  - `app/main.py` — FastAPI ASGI (`app.main:app`)
  - `app/run_worker.py` — entrypoint worker (`app.run_worker:main`)
  - `app/worker.py` — vòng lặp worker
  - `app/webhook.py` — router nhận TradingView
  - `app/risk_guard.py` — validate payload (chỉ `gsalgovip_v1`)
  - `app/state_store.py` — state store **PostgreSQL** qua `psycopg` 3
  - `app/mt5_executor.py` — executor MT5 (runtime Windows)
  - `app/models.py` — dataclass thuần
  - `app/config.py` — loader env (DATABASE_URL v.v.)
  - `app/logger.py` — logging stdlib
  - `app/telegram_notify.py` — Telegram tùy chọn
  - `app/runner_impl.py` — stub entrypoint an toàn cho platform
    (không bao giờ trade, không import MT5, không mở DB connection)

## Package này KHÔNG có (và vì sao)

- Không có script PowerShell start — vòng đời process thuộc về Windows
  runner manager của platform
- Không hardcode đường dẫn MT5 terminal — platform inject lúc runtime
- Không có MT5 login / magic / broker server cụ thể — riêng tenant,
  platform inject
- Không có webhook secret, không có MT5 password, không có Telegram
  token, **không có DATABASE_URL** — mọi secret nằm trong secret store
  của platform
- Không SQLite — đã thay bằng PostgreSQL
- Không ghi vào core PostgreSQL của Linux backend — bot có DB RIÊNG

## Contract cô lập database

`platform_contract` và `data_store` cùng nhau cưỡng chế:

1. Platform **PHẢI** cấp một database PostgreSQL (hoặc schema) tách rời
   cho bot này, cô lập khỏi core DB của Linux backend.
2. Bot dùng `psycopg` để kết nối qua `DATABASE_URL`. Nó chạy DDL
   `CREATE TABLE IF NOT EXISTS` ở lần kết nối đầu, scope trong DB của
   chính nó.
3. Bot **KHÔNG ĐƯỢC** đọc/ghi bất cứ bảng nào không khai báo trong
   `data_store.tables`.
4. Bố cục khuyến nghị:
   - **Per-bot DB**: `gsalgovip_t1`, `gsalgovip_t2`, ...
   - **Per-bot schema** trong shared bots DB: `bots.gsalgovip_t1`, ...
   - **Single bot DB lọc theo `config_key`** cho deployment ít tenant

## Contract strategy (phía TradingView)

Risk guard của package này chỉ chấp nhận TradingView alert nếu:

- `source` == `"tradingview"`
- `strategy` == `"gsalgovip_v1"`  *(khác với `gsalgo_v1` của gsalgo)*
- `event_type` == `"ENTRY"`
- `timeframe` ∈ `{M1, 1}`
- `side` ∈ `{BUY, SELL}` với hình học SL/Entry/TP hợp lệ
- `is_confirmed` true
- `config_key` và `nonce` không rỗng

`nonce` PHẢI duy nhất cho mỗi alert; replay sẽ trả `{"status":"duplicate"}`.

## Validate cục bộ (read-only, không kết nối DB, không trade)

Các lệnh sau chỉ validate manifest + config + cú pháp Python:

```bash
python3 -c "import json; json.load(open('bot_manifest.json'))"
python3 -c "import json; json.load(open('config/schema.json'))"
python3 -c "import json; json.load(open('config/default.json'))"
python3 -c "import ast; ast.parse(open('app/runner_impl.py').read())"
python3 -c "import ast; ast.parse(open('app/state_store.py').read())"
```

Import live `app.state_store` đòi `psycopg` đã cài và `DATABASE_URL`
truy cập được — chỉ làm trên Windows runner / tenant test DB.

## Triển khai đa tenant (TL;DR)

- Một process OS / một tenant. Platform spawn nó với block env riêng cho
  tenant.
- Bản thân package **không có khái niệm tenant trong code** — chỉ tin
  vào env. Không có nhánh `if tenant == ...` ở bất cứ đâu.
- Mỗi tenant, platform phải cấp: `APP_PORT` duy nhất, `DATABASE_URL`
  duy nhất (DB riêng hoặc schema riêng), `WEBHOOK_SECRET` duy nhất, bộ
  `MT5_*` duy nhất, MT5 slot/đường dẫn terminal duy nhất, kèm
  `TENANT_ID` / `INSTANCE_ID` để observability.
- URL webhook của một tenant trông như
  `https://api.cntx.com/t/<tenant_id>/webhook/tradingview` (path prefix
  do nginx route → host:port của runner).
- Cô lập khi sự cố: process tenant A crash không ảnh hưởng tenant B —
  process khác nhau, MT5 slot khác nhau, Postgres DB khác nhau.

Contract đầy đủ & lệnh spawn runner: xem
[`bot-trading/PLATFORM_INTEGRATION_PLAN.md`](../PLATFORM_INTEGRATION_PLAN.md)
và [`./INTEGRATION_NOTES.md`](./INTEGRATION_NOTES.md).

## Những việc package này KHÔNG được làm

- Package KHÔNG ĐƯỢC kill process
- KHÔNG ĐƯỢC tự chọn đường dẫn MT5 terminal
- KHÔNG ĐƯỢC đọc credential từ disk ngoài runtime context
- KHÔNG ĐƯỢC ghi trực tiếp vào core PostgreSQL của Linux backend
- KHÔNG ĐƯỢC gọi Redis trực tiếp
- KHÔNG ĐƯỢC hardcode bất cứ đường dẫn production nào

Các contract này cũng được mã hóa trong
`bot_manifest.json -> platform_contract`.
