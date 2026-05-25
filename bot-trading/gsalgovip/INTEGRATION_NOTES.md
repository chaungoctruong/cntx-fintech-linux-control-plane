# GsAlgoVIP — Ghi chú tích hợp riêng cho bot này

File này chỉ chứa **những phần đặc thù của `gsalgovip`**.
Kế hoạch platform chung và contract package bot nằm ở thư mục cha:

- [`bot-trading/PACKAGE_STANDARD.md`](../PACKAGE_STANDARD.md) — contract
  mọi bot phải tuân (manifest, layout, env, health, log, risk, secret).
- [`bot-trading/PLATFORM_INTEGRATION_PLAN.md`](../PLATFORM_INTEGRATION_PLAN.md)
  — kế hoạch tích hợp Linux/Windows/nginx không phụ thuộc bot, áp dụng
  được cho `gsalgovip` và mọi bot tương lai.

Nếu bất cứ chỗ nào dưới đây mâu thuẫn với 2 doc kia, 2 doc kia thắng.

---

## 1. Tuyên bố tuân thủ

`gsalgovip` v0.3.0 tuân thủ PACKAGE_STANDARD v1:

| Yêu cầu của tiêu chuẩn | Trạng thái gsalgovip |
|---|---|
| `manifest_version: 1` | ✓ |
| `bot_id` khớp tên thư mục + regex | ✓ (`gsalgovip`) |
| `deployment_model.kind = one_process_per_tenant` | ✓ |
| `data_store.kind = postgresql` + `must_be_separate_from_linux_core_db = true` | ✓ |
| `platform_contract.must_not_*` toàn bộ true | ✓ |
| `platform_contract.tenant_isolated = true` | ✓ |
| Stub an toàn `app.runner_impl:run` (không MT5/network/PG) | ✓ |
| `/healthz` trả `tenant_id, instance_id, dry_run, trading_enabled` | ✓ |
| Dòng log có prefix `[<tenant_id>@<instance_id>]` | ✓ |
| `.env.example` không chứa giá trị secret nào | ✓ |
| Strategy tag `gsalgovip_v1` được enforce trong `risk_guard` | ✓ |
| `db/schema.sql` idempotent (CREATE TABLE IF NOT EXISTS) | ✓ |

---

## 2. Các knob cấu hình riêng của gsalgovip

Những thứ platform cần biết mà ĐẶC THÙ của bot này (không suy ra được
từ tiêu chuẩn chung):

### 2.1 Port webhook mặc định

`8017` — khai báo trong `manifest.resource_hints.default_webhook_port`.
Pool port của runner có thể override theo từng tenant; giá trị này chỉ là
gợi ý cho dev đơn lẻ hoặc smoke run single-tenant.

### 2.2 Dải MT5 magic number

Bot này dùng magic number trong dải `420000–429999` (mỗi tenant một
magic). Khi provisioner cấp phát cho một tenant, chọn `MT5_MAGIC =
420000 + tenant_index` trong đó `tenant_index` là duy nhất trong namespace
của bot này.

Nếu một bot khác (ví dụ `botX`) dùng dải magic chồng lấn trên cùng broker
MT5, báo cáo phía broker có thể đụng độ. Provisioner của platform nên
track dải magic per-bot trong một registry nhỏ trong DB.

### 2.3 Symbol

Symbol mặc định: `XAUUSD`. Risk guard của bot chỉ nhận một symbol mỗi
alert. Setup nhiều symbol đòi chạy bot này 2 lần cho cùng tenant (config
khác nhau) — có hỗ trợ qua 2 row `(bot_code, tenant_id)` không? **Không**
— mô hình subscription chuẩn là 1 row / `(bot_code, tenant_id)`. Đa
symbol thì tenant phải subscribe 2 lần với `instance_id` override khác
nhau. Sẽ quyết định sau xem có phải blocker không.

### 2.4 Strategy tag TradingView

Alert PHẢI set `"strategy": "gsalgovip_v1"`. Bất kỳ tag khác → 400 từ
webhook router. Quy tắc này được enforce trong `app/risk_guard.py`:

```python
ALLOWED_STRATEGY_NAMES = ("gsalgovip_v1",)
```

Khi bump major version (ví dụ v2), cập nhật `ALLOWED_STRATEGY_NAMES` để
bao gồm `"gsalgovip_v2"` và cung cấp `tools/migrate_v1_to_v2.py` theo
PACKAGE_STANDARD §13.

### 2.5 Telemetry Telegram

`TELEGRAM_BOT_TOKEN` và `TELEGRAM_CHAT_ID` nằm trong
`manifest.secrets_optional`. Nếu platform không inject, bot lặng lẽ chạy
mà không có notification (không lỗi).

---

## 3. Smoke test riêng cho `gsalgovip`

Chạy sau khi platform hoàn tất harness smoke per-bot trạng thái-ổn-định
(P8 trong platform plan). Mọi bước giả định tenant `t-smoke`:

1. **Subscribe**

   `POST /api/bots/gsalgovip/subscribe` với body
   ```json
   {
     "lot_size": 0.01,
     "stop_loss": 50,
     "take_profit": 100,
     "symbol": "XAUUSD",
     "timeframe": "M1",
     "trading": { "enabled": false, "dry_run": true }
   }
   ```
   → response chứa `webhook_url`, `webhook_secret` một lần duy nhất.

2. **Kiểm tra provision** (phía Linux)

   `psql $BOTS_PG_ADMIN_URL -c '\l gsalgovip_t_smoke'` → DB tồn tại.
   Vault có `secret/bots/gsalgovip/t-smoke/{database_url,webhook_secret,mt5_password}`.

3. **Kiểm tra spawn ở runner** (phía Windows)

   Trên runner được gán, 2 process xuất hiện trong 60s:
   - `python -m uvicorn app.main:app --host 0.0.0.0 --port <pool>`
   - `python -m app.run_worker`
   Cả hai có env `TENANT_ID=t-smoke`, `INSTANCE_ID=t-smoke-<runner>-<slot>`.

4. **Healthz**

   `curl http://<runner>:<port>/healthz` →
   ```json
   {"status":"ok","bot":"gsalgovip","tenant_id":"t-smoke",
    "instance_id":"t-smoke-runner-w2-slot-3","dry_run":"true",
    "trading_enabled":"false"}
   ```

5. **Alert tổng hợp**

   `POST <webhook_url>` với payload `gsalgovip_v1` hợp lệ.

6. **Row trong PG riêng của bot**

   `psql $DATABASE_URL_for_gsalgovip_t_smoke -c "SELECT id, status FROM signals;"`
   → 1 row, `status='dry_run'`.

7. **Kiểm tra dòng log**

   Tail log của runner → tìm `[t-smoke@t-smoke-runner-w2-slot-3] gsalgovip_boot ...`.

Nếu cả 7 pass với `DRY_RUN=true`, flip `TRADING_ENABLED=true` và lặp lại
trên một MT5 demo thật trước khi mở cho tenant production.

---

## 4. Hạn chế đã biết & câu hỏi mở

- **Hôm nay 1-symbol/process.** Đa symbol cùng tenant đòi 2 subscription
  `(bot_code, tenant_id)` khác nhau — chưa được khóa chính của bảng
  subscription hỗ trợ. Câu hỏi mở.
- **Mỗi query mở 1 connection trong `state_store.py`.** Ổn tới ~50
  alert/min/tenant. Vượt mức đó, chuyển sang `psycopg_pool.ConnectionPool`.
  Việc này nằm trong scope `bot-trading/gsalgovip/app/state_store.py`.
- **Webhook auth = so sánh chuỗi thuần** với `WEBHOOK_SECRET`. Chấp
  nhận được trên HTTPS nhưng chưa best-in-class. Cải thiện sau launch
  (HMAC trên body, chống replay bằng timestamp).

---

## 5. File này KHÔNG phải

- Nó **không** định nghĩa lại contract platform (xem PACKAGE_STANDARD.md).
- Nó **không** lặp lại kế hoạch tích hợp platform (xem
  PLATFORM_INTEGRATION_PLAN.md).
- Nó **không** phải `README.md` (xem [`README.md`](./README.md) cho người
  cài đặt package).

Nó *chỉ* là các quyết định riêng của gsalgovip mà kỹ sư platform cần biết
khi nối bot này vào pipe chung.
