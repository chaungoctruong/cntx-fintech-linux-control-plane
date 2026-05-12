# Tiêu chuẩn Bot Package — CNTx Labs / Spider AI v1

**Đối tượng:** bất kỳ ai viết một bot mới đặt dưới `bot-trading/`.
**Trạng thái:** v1 — đã chốt cho production.
**Bản tham chiếu mẫu:** `bot-trading/gsalgovip/` (manifest_version: 1).

Tiêu chuẩn này tồn tại để platform (Linux backend, Windows runner, nginx,
frontend) có thể vận hành **N bot × M tenant** mà không cần code riêng cho
từng bot. Một "bot package" là bất cứ thứ gì tuân theo tài liệu này.
Platform **không biết gì** về nội bộ một bot cụ thể — nó chỉ đọc manifest và
tin vào các cam kết bên dưới.

> Nếu một bot package vi phạm bất kỳ mục *MUST* nào trong tài liệu này, nó
> sẽ bị catalog loader từ chối và không được phân phối.

---

## 1. Bố cục thư mục (BẮT BUỘC)

```
bot-trading/<bot_id>/
├── bot_manifest.json          # bắt buộc, manifest_version: 1
├── VERSION                    # bắt buộc, semver, phải khớp manifest.version
├── README.md                  # bắt buộc, dành cho người đọc
├── config/
│   ├── schema.json            # bắt buộc, JSON Schema 2020-12, KHÔNG chứa secret
│   └── default.json           # bắt buộc, hợp lệ với schema, mặc định an toàn (dry-run)
├── app/                       # bắt buộc, source code package
│   ├── runner_impl.py         # bắt buộc, entrypoint stub an toàn (xem §6)
│   └── ...                    # source riêng của bot
├── db/                        # tùy chọn; bắt buộc nếu data_store.kind != "none"
│   └── schema.sql             # DDL idempotent (CREATE TABLE IF NOT EXISTS)
├── requirements.txt           # bắt buộc nếu runtime_language == "python"
├── .env.example               # bắt buộc, MỌI giá trị phải rỗng (chỉ là placeholder)
└── .gitignore                 # bắt buộc, loại trừ logs/secrets/data
```

`<bot_id>` PHẢI khớp regex `^[a-z][a-z0-9_]{2,31}$` và bằng `manifest.bot_id`.

Owner sở hữu nhiều bot CÓ THỂ dùng layout 2 cấp
`bot-trading/<owner>/<bot_id>/`. Catalog loader xử lý cả hai độ sâu.

---

## 2. Lược đồ manifest (BẮT BUỘC)

`bot_manifest.json` là nguồn sự thật duy nhất mà platform đọc.
**Các key bắt buộc ở mức top-level** (v1):

| Key | Kiểu | Ghi chú |
|---|---|---|
| `manifest_version` | int | phải bằng `1` |
| `bot_id` | string | khớp `<bot_id>` của thư mục |
| `bot_code` | string | thường bằng `bot_id` (dùng làm path API) |
| `bot_name` | string | tên hiển thị cho người dùng |
| `owner` | string | ví dụ `cntx_labs` |
| `package_path` | string | ví dụ `bot-trading/<bot_id>` |
| `version` | semver string | khớp file `VERSION` |
| `description` | string | bot này làm gì |
| `runtime_language` | string | `python`, `node`, ... (v1 chỉ hỗ trợ `python`) |
| `runtime_python` | string | dải phiên bản theo PEP 440, bắt buộc nếu là python |
| `entrypoint` | string | `app.runner_impl:run` — stub an toàn (§6) |
| `legacy_entrypoints` | object | `fastapi_asgi`, `worker_main` — runner sẽ spawn cái này |
| `profile_class` | string | `light`, `normal`, `heavy` — gợi ý sizing |
| `strategy_tags` | array<string> | tag để tìm kiếm |
| `required_params` | array<string> | các config key top-level bắt buộc phải set |
| `deployment_model` | object | xem §3 |
| `data_store` | object | xem §4 |
| `resource_hints` | object | xem §5 |
| `risk_contract` | object | xem §7 |
| `config_schema` | string | đường dẫn tới JSON Schema (tương đối với package) |
| `default_config_path` | string | đường dẫn tới default config an toàn |
| `secrets_required` | array<string> | CHỈ tên env, không bao giờ chứa giá trị |
| `secrets_optional` | array<string> | CHỈ tên env |
| `secrets_source` | string | phải là `runtime_context` |
| `platform_contract` | object | xem §8 |

**Manifest KHÔNG ĐƯỢC chứa:** bất kỳ giá trị secret, đường dẫn production,
hostname/IP, hoặc tenant identifier nào.

---

## 3. `deployment_model` (BẮT BUỘC)

```json
{
  "kind": "one_process_per_tenant",
  "isolation": "per_tenant_env_block",
  "shared_runner_supported": true,
  "tenant_routing": "platform_owned",
  "tenant_envs_per_process": [
    "TENANT_ID", "INSTANCE_ID", "APP_PORT", "WEBHOOK_PATH",
    "DATABASE_URL", "WEBHOOK_SECRET",
    "MT5_TERMINAL_PATH", "MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "MT5_MAGIC"
  ],
  "port_strategy": "platform_assigns_unique_port_per_tenant",
  "webhook_path_strategy": "default_or_platform_overrides_per_tenant",
  "logs_strategy": "instance_label_prefix_in_each_line"
}
```

**Các giá trị `kind` được phép trong v1:**

- `one_process_per_tenant` — mặc định, khuyến nghị. Một process OS cho mỗi
  tenant trên mỗi runner. Cô lập tối đa, dễ suy luận. `gsalgovip` dùng cách này.
- `singleton_global` — một process phục vụ TẤT CẢ tenant bằng cách phân kênh
  qua payload. Chỉ được phép nếu bot không có MT5 credential riêng cho từng
  tenant. Hiếm — cần security review.
- `pool_of_processes_with_affinity` — pool N process; tenant `t` luôn route
  về `hash(t) % N`. Dành cho bot stateless cực rẻ. Để dành cho v2.

Mảng `tenant_envs_per_process` cho platform biết cần inject gì cho mỗi
tenant. Đây là contract *đầy đủ*; platform sẽ từ chối spawn bot nếu không
populate được mọi tên trong list.

---

## 4. `data_store` (BẮT BUỘC)

Bot sở hữu data của chính nó. Platform KHÔNG cho bot chạm vào core DB của
Linux backend.

```json
{
  "kind": "postgresql",                    // hoặc "none"
  "isolation": "per_bot_or_per_tenant",
  "must_be_separate_from_linux_core_db": true,
  "schema_file": "db/schema.sql",
  "auto_init_on_startup": true,
  "tables": ["signals", "executions"],
  "min_postgres_version": "13",
  "concurrency_pattern": "FOR UPDATE SKIP LOCKED",
  "url_env": "DATABASE_URL"
}
```

Nếu `kind == "none"`, các key khác có thể bỏ. Ngược lại:

- `must_be_separate_from_linux_core_db` PHẢI là `true`. Provisioner từ chối
  cấp `DATABASE_URL` trỏ về cluster core của platform.
- `auto_init_on_startup` NÊN là `true`. Bot chạy DDL idempotent ở lần kết
  nối đầu. Platform tạo DB rỗng; bot tự tạo bảng.
- `concurrency_pattern` mô tả nhiều worker dùng chung queue an toàn ra sao.
  Hiện tại chỉ chấp nhận `FOR UPDATE SKIP LOCKED`.
- `url_env` là tên env biến mang chuỗi kết nối. Theo quy ước là
  `DATABASE_URL`.

**Quy ước đặt tên DB per-tenant** (provisioner dùng):

- DB: `<bot_code>_<tenant_id>` (ví dụ `gsalgovip_t42`)
- Role: tên giống DB
- Connection: `postgresql://<role>:<random>@<bots_pg_host>:5432/<db>`

---

## 5. `resource_hints` (BẮT BUỘC để sizing)

```json
{
  "runtime": "windows_mt5",
  "lane": "mt5_runner",
  "requires_mt5": true,
  "requires_single_slot": true,
  "requires_clean_terminal_on_start": true,
  "requires_terminal_kill_on_stop": true,
  "max_symbols": 1,
  "default_symbols": ["XAUUSD"],
  "needs_inbound_webhook": true,
  "requires_postgres": true,
  "default_webhook_port": 8017,
  "memory_hint_mb": 256,
  "cpu_hint_cores": 0.25
}
```

Port pool của runner được cấp THEO TỪNG RUNNER, không theo bot.
`default_webhook_port` chỉ là gợi ý cho dev đơn lẻ; runner chọn port thật
ở thời điểm spawn.

---

## 6. Stub entrypoint an toàn `app.runner_impl:run` (BẮT BUỘC)

Platform gọi entrypoint này khi muốn **kiểm tra/quan sát** bot (catalog
scan, dry-run, smoke). Nó KHÔNG ĐƯỢC:

- import MetaTrader5 / module chỉ chạy được khi có `MetaTrader5`
- mở socket mạng
- mở kết nối Postgres
- start thread làm việc
- gọi `os.kill`, `subprocess`, `taskkill`

Nó PHẢI:

- nhận object `ctx`: `ctx.config: dict`, `ctx.stop_event: threading.Event`,
  `ctx.logger: logging.Logger`
- log một dòng `<bot_id>_runner_startup config_keys=...`
- block ở `ctx.stop_event.wait()` và return khi có signal
- không bao giờ raise

Runtime trading thật là `legacy_entrypoints.fastapi_asgi` và
`legacy_entrypoints.worker_main`. Runner spawn các cái đó như process riêng;
stub an toàn này dành cho *platform tự kiểm tra*.

Tham chiếu (gsalgovip):

```python
def run(ctx) -> None:
    ctx.logger.info("gsalgovip_runner_startup config_keys=%s", sorted(ctx.config))
    ctx.stop_event.wait()
```

---

## 7. `risk_contract` (BẮT BUỘC cho bot trading)

```json
{
  "requires_sl": true,
  "requires_tp": true,
  "max_orders": 20,
  "max_basket": 1,
  "max_order_per_minute": 10,
  "max_modify_per_minute": 30,
  "default_volume_min": 0.01,
  "default_volume_max": 1.0,
  "trading_disabled_by_default": true,
  "dry_run_by_default": true
}
```

Platform áp các giá trị này như cận trên. Config của tenant có thể giảm
nhưng không bao giờ vượt. `trading_disabled_by_default == true` và
`dry_run_by_default == true` nghĩa là tenant vừa được provision thì AN TOÀN
— không có lệnh thật cho tới khi tự mình bật.

---

## 8. `platform_contract` (BẮT BUỘC)

Đây là *cam kết* của bot package với platform. Mọi key PHẢI có mặt và set
rõ ràng.

```json
{
  "receives_runtime_context": true,
  "receives_stop_event": true,
  "must_not_kill_processes": true,
  "must_not_choose_terminal_path": true,
  "must_not_write_postgres_core": true,
  "owns_its_own_postgres_db": true,
  "must_not_call_redis_directly": true,
  "must_not_hardcode_production_paths": true,
  "lifecycle_managed_by_platform": true,
  "tenant_isolated": true,
  "platform_must_provision_per_tenant": [
    "unique_app_port",
    "unique_database_url",
    "unique_webhook_secret",
    "unique_mt5_credentials",
    "unique_mt5_terminal_path",
    "tenant_id_env",
    "instance_id_env"
  ]
}
```

Giá trị của các `must_not_*` và `tenant_isolated` PHẢI là `true`. Catalog
loader sẽ từ chối bất cứ manifest nào khai báo `false`.

---

## 9. Config & secret

### 9.1 Config schema (`config/schema.json`) — BẮT BUỘC

- JSON Schema 2020-12.
- PHẢI validate *toàn bộ* surface config mà tenant chỉnh được.
- KHÔNG ĐƯỢC chứa field `secret`. Secret chỉ đi qua env.
- NÊN dùng `additionalProperties: false` ở top-level để key lạ bị báo lỗi.

### 9.2 Default config (`config/default.json`) — BẮT BUỘC

- Hợp lệ với `config/schema.json`.
- PHẢI set `trading_enabled: false` và `dry_run: true` nếu các field này tồn tại.
- PHẢI set volume mặc định = mức nhỏ nhất bot hỗ trợ (khác 0).
- KHÔNG ĐƯỢC chứa account / login / token / URL nào.

### 9.3 Secret — BẮT BUỘC

- Chỉ liệt kê **tên** trong `manifest.secrets_required` / `secrets_optional`.
- Chỉ truyền qua process environment, KHÔNG qua file.
- `.env.example` PHẢI tồn tại để document; MỌI giá trị PHẢI rỗng.
- Bot PHẢI mask credential khi log (ví dụ `database_url_safe`).

---

## 10. Biến môi trường runtime (bot đọc, platform ghi)

Mọi bot tuân theo tiêu chuẩn này đều đọc các env sau khi boot:

| Env | Bắt buộc? | Ai set | Ghi chú |
|---|---|---|---|
| `TENANT_ID` | có | platform | ID per-tenant (chỉ để log) |
| `INSTANCE_ID` | có | platform | ID per-process (chỉ để log) |
| `APP_HOST` | tùy chọn | platform | Mặc định `0.0.0.0` |
| `APP_PORT` | có | platform | Duy nhất per-tenant trên mỗi runner |
| `WEBHOOK_PATH` | tùy chọn | platform | Mặc định `/webhook/tradingview` |
| `LOG_PATH` | tùy chọn | platform | Mặc định `${CNTX_LOG_DIR:-<repo>/logs}/runner/<bot_id>.log` |
| `DATABASE_URL` | có (nếu data_store != none) | platform | PG riêng của bot, mask khi log |
| `WEBHOOK_SECRET` | có (nếu cần webhook vào) | platform | Random 32B base64 per-tenant |
| `TRADING_ENABLED` | có | platform | Mặc định `false` |
| `DRY_RUN` | có | platform | Mặc định `true` |
| `MT5_*` | có (nếu requires_mt5) | platform | MT5 credential + slot per-tenant |
| `TELEGRAM_*` | tùy chọn | platform | Notification |

Bot PHẢI coi các env này là interface ĐẦY ĐỦ. Không đọc secret từ disk,
không tự auto-discovery, không có fallback "default" account.

---

## 11. Health & observability (BẮT BUỘC)

### 11.1 `/healthz`

Mọi bot có webhook PHẢI mở `GET /healthz` trả về tối thiểu:

```json
{
  "status": "ok",
  "bot": "<bot_id>",
  "tenant_id": "<tenant_id hoặc rỗng>",
  "instance_id": "<instance_id hoặc rỗng>",
  "dry_run": "true|false",
  "trading_enabled": "true|false"
}
```

### 11.2 Định dạng log

Mọi dòng log PHẢI có prefix `[<tenant_id>@<instance_id>]` để aggregator
chung (loki, fluent-bit) tách stream per-tenant mà không phải parse log level.

### 11.3 Boot log

Bot PHẢI log một dòng boot gồm: `bot_id`, `version`, `tenant_id`,
`instance_id`, `database_url` đã mask, `app_port`, `dry_run`,
`trading_enabled`. Đây là dòng người ta grep khi xử lý sự cố.

---

## 12. Quy ước strategy tag

Nếu bot nhận alert từ ngoài (TradingView v.v.), payload alert PHẢI có
`strategy: "<bot_id>_v<major_version>"` (ví dụ `gsalgovip_v1`). `risk_guard`
của bot PHẢI từ chối mọi tên strategy khác.

Quy tắc này đảm bảo tenant không thể vô tình route alert `botX_v1` vào
process `botY_v1`.

---

## 13. Versioning (BẮT BUỘC)

- Semver. `major.minor.patch`.
- Bump MAJOR: thay đổi config schema phá vỡ backward-compat. Platform yêu
  cầu kèm migration tool (`tools/migrate_<from>_to_<to>.py`) trước khi cho
  upgrade tenant đang chạy major cũ.
- MINOR/PATCH: backward-compatible. Platform có thể auto-upgrade sau staging.
- File `VERSION` PHẢI bằng `manifest.version`.

---

## 14. Bot KHÔNG ĐƯỢC làm gì (luật cứng)

- Chạm vào core PG / Redis / queue của Linux backend. Vi phạm trực tiếp
  → reject ở catalog scan.
- Kill process. Vòng đời do platform sở hữu.
- Tự chọn đường dẫn MT5 terminal. Cấp slot do runner sở hữu.
- Hardcode hostname production, URL, account number, tenant ID.
- Đọc credential từ disk ngoài runtime env.
- Loop mà không tôn trọng `stop_event` (sẽ leak process).
- Block event loop trong code async (sẽ làm đói tenant khác).

Catalog loader chạy automated check cho các luật "MUST NOT" tại lúc đăng
ký package. Vi phạm bất kỳ → manifest bị reject, bot không xuất hiện
trong catalog, không tenant nào subscribe được.

---

## 15. Cách platform tích hợp một bot tuân thủ tiêu chuẩn

Xem `bot-trading/PLATFORM_INTEGRATION_PLAN.md` để biết kế hoạch tích hợp
(catalog loader, provisioner, runner spawner, nginx routing, frontend).

Kế hoạch đó **không phụ thuộc bot cụ thể** — thêm `botX` = thả một
package tuân thủ vào `bot-trading/botX/`. Không phải sửa code platform.

---

## 16. Bản tham chiếu mẫu

- **`bot-trading/gsalgovip/`** — bản tham chiếu chính thức.
  `manifest_version 1`, `kind: one_process_per_tenant`,
  `data_store.kind: postgresql`, `requires_mt5: true`. Tác giả của bot mới
  NÊN copy từ `gsalgovip`, đổi `bot_id`, sửa logic strategy, giữ nguyên
  bộ khung manifest.
- **`bot-trading/gsalgo/`** — tổ tiên đã đóng-băng-cho-production của
  `gsalgovip`. KHÔNG tuân thủ v1 ở vài chi tiết (single-tenant, hardcoded
  path). Sẽ bị deprecate; không dùng làm template.
