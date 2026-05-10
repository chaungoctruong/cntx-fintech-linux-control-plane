# Kế hoạch tích hợp Platform — SaaS đa-bot, đa-tenant

**Đối tượng:** Linux backend dev, Windows runner dev, DevOps/Infra, Frontend.
**Tài liệu đi kèm:** `bot-trading/PACKAGE_STANDARD.md` (contract phía bot).
**Trạng thái:** ĐỀ XUẤT — chờ user duyệt từng phase.

Kế hoạch này **không phụ thuộc bot cụ thể**. Mỗi phase được tham số hóa
theo `bot_code`. Sau khi triển khai xong, thêm bot mới = thả một package
tuân thủ vào `bot-trading/<new_bot>/`. **Không phải sửa code platform cho
mỗi bot mới.**

Bot tham chiếu đầu tiên là `gsalgovip`, nhưng mọi API, bảng, env var và
rule routing bên dưới đều dùng `bot_code` như biến, không phải hằng.

---

## 0. Vì sao phải bot-agnostic ngay từ đầu

Production sẽ có **N bot × M tenant**. Hai anti-pattern chúng ta cố tránh:

- *Hardcode bot id trong route* (`/webhook/gsalgovip` khắp nơi) — mỗi bot
  mới sẽ đòi sửa cả nginx + frontend + backend.
- *Bảng riêng cho từng bot* (`gsalgovip_subscriptions`, `botX_subscriptions`,
  ...) — mỗi bot mới sẽ đòi một migration mới.

Thay vào đó, chúng ta dùng một bộ primitive chung được key bằng
`(bot_code, tenant_id)`.

---

## 1. Mô hình data dùng chung

### 1.1 Catalog (chủ yếu read, populate lúc khởi động từ manifest)

Bảng `bot_catalog` đã có — kế hoạch này không thêm gì cho nó. Manifest
trong `bot-trading/<bot_id>/bot_manifest.json` là nguồn sự thật. Catalog
loader (P1) populate bảng lúc boot.

### 1.2 Bảng mới: `tenant_bot_subscription`

```sql
CREATE TABLE tenant_bot_subscription (
    tenant_id       TEXT        NOT NULL,
    bot_code        TEXT        NOT NULL,
    package_version TEXT        NOT NULL,
    runner_id       TEXT        NULL,           -- nullable cho tới khi P3 schedule
    app_port        INT         NULL,
    mt5_slot_id     TEXT        NULL,
    database_url_vault_key  TEXT NOT NULL,
    webhook_secret_vault_key TEXT NOT NULL,
    status          TEXT        NOT NULL,       -- 'pending', 'running', 'stopped', 'archived'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, bot_code)
);
CREATE INDEX ON tenant_bot_subscription (runner_id, status);
CREATE INDEX ON tenant_bot_subscription (bot_code, status);
```

Khóa chính phức hợp `(tenant_id, bot_code)` là key duy nhất mà provisioner,
runner spawner, frontend và nginx đều tham chiếu.

### 1.3 Layout vault

Path key theo `(bot_code, tenant_id)`:

```
secret/bots/<bot_code>/<tenant_id>/database_url
secret/bots/<bot_code>/<tenant_id>/webhook_secret
secret/bots/<bot_code>/<tenant_id>/mt5_password
secret/bots/<bot_code>/<tenant_id>/telegram_token   (tùy chọn)
```

Runner đọc các path khai báo trong `manifest.secrets_required` /
`secrets_optional`. Bot mới = manifest mới = path vault mới, *không phải
sửa code platform*.

---

## 2. Đồ thị phụ thuộc giữa các phase

```
[P1] catalog_loader_generic ──┬──► [P5] frontend_lists_all_bots ──► [P6] generic_subscribe_endpoint
                              │
                              ├──► [P2] generic_provisioner ──► [P3] generic_runner_reconciler ──► [P4] generic_nginx_router ──► [P8] e2e_smoke_per_bot
                              │                                       ▲
                              └──► [P7] subscription_table ──────────┘
                                                                                                                                   [P9] (per bot) deprecate_legacy
```

---

## P1 · Catalog loader generic

**Mục tiêu:** Backend nhận diện *mọi* package dưới `bot-trading/` tuân
thủ `PACKAGE_STANDARD.md`.

| Trường | Giá trị |
|---|---|
| **Owner** | Linux backend dev |
| **File** | `backend_ai/backend/app/bot_catalog/mt5_repository_loader.py` |
| **Bot-agnostic?** | CÓ. Loader walk `bot-trading/*/bot_manifest.json` (depth 1) VÀ `bot-trading/*/*/bot_manifest.json` (depth 2). Validate mọi manifest theo schema v1. Reject manifest vi phạm bất kỳ rule "MUST" / "MUST NOT". |
| **Validation** | (a) `manifest_version == 1`, (b) `bot_id` khớp tên thư mục và regex, (c) `platform_contract.must_not_*` toàn bộ true, (d) `platform_contract.tenant_isolated == true`, (e) `data_store.must_be_separate_from_linux_core_db == true` (nếu data_store.kind != "none"), (f) `secrets_required` chỉ chứa tên (không chứa giá trị). |
| **Hành xử khi vi phạm** | Manifest bị reject, bot KHÔNG xuất hiện trong catalog, log warning có cấu trúc. Các bot tuân thủ khác load bình thường. |
| **Rủi ro** | Thấp. Chỉ đọc. Đặt feature flag `MT5_CATALOG_ALLOW_SINGLE_LEVEL=true`. |
| **Acceptance test** | Hôm nay: `gsalgovip` xuất hiện cùng `gsalgo`. Mai: thả `bot-trading/botX/` với manifest tuân thủ → `botX` xuất hiện không phải sửa code. Thả `bot-trading/badbot/` với `must_not_kill_processes: false` → `badbot` bị reject, các bot khác không bị ảnh hưởng. |
| **Effort** | 1 ngày (loader + manifest validator) |

---

## P2 · Provisioner generic per-(bot, tenant)

**Mục tiêu:** Một service duy nhất, cho bất kỳ `(bot_code, tenant_id)`,
provision Postgres DB + secret + entry vault, không phụ thuộc bot nào.

| Trường | Giá trị |
|---|---|
| **Owner** | Linux backend dev |
| **File** | `backend_ai/backend/app/services/bot_provisioner.py` (mới), `backend_ai/backend/app/services/bots_postgres_admin.py` (mới), `settings.py` (env mới: `BOTS_POSTGRES_ADMIN_URL`, `BOTS_POSTGRES_HOST_FOR_RUNNER`, `BOTS_VAULT_BACKEND`) |
| **API public** | `provisioner.provision(bot_code: str, tenant_id: str) -> ProvisionResult` và `provisioner.deprovision(bot_code, tenant_id)`. Provisioner đọc manifest của bot để biết secret nào cần generate (không hardcode danh sách). |
| **Bot-agnostic?** | CÓ. `secrets_required` của manifest điều khiển cái gì được generate. Nếu `manifest.data_store.kind == "none"`, không tạo DB. Nếu `manifest.requires_mt5`, ô `mt5_password` trong vault được reserve. |
| **Đặt tên DB** | `<bot_code>_<tenant_id>` cho cả DB lẫn role. Tenant_id sanitize về `[a-z0-9_]`. |
| **Cluster** | `BOTS_POSTGRES_ADMIN_URL` PHẢI trỏ tới cluster *tách rời* khỏi Linux core. Provisioner refuse chạy nếu host trùng `LINUX_CORE_PG_HOST`. |
| **Vault** | Backend pluggable (env: `BOTS_VAULT_BACKEND=hashicorp_vault\|file_encrypted\|aws_secrets`). v1 ship `file_encrypted` cho dev + `hashicorp_vault` cho prod. |
| **Rủi ro** | Trung. Privileged Postgres ops. Mitigate: role admin riêng có `CREATEDB`/`CREATEROLE` CHỈ trên cluster bots. Idempotent: re-provision cùng `(bot_code, tenant_id)` là no-op nếu vault entry trùng. |
| **Rollback** | `deprovision(bot_code, tenant_id)` drop DB + role + entry vault. Soft-archive row trong `tenant_bot_subscription`. |
| **Acceptance test** | Provision 2 bot khác nhau cho cùng tenant: `provision("gsalgovip", "t-smoke")` và `provision("botX", "t-smoke")` → 2 DB tách rời, 2 entry vault tách rời, 2 secret tách rời. Provision cùng bot cho 2 tenant: cùng dạng. |
| **Phụ thuộc** | P1 (manifest phải hợp lệ trước khi provision), P7 (bảng subscription) |
| **Effort** | 2 ngày (provisioner + admin + adapter vault) |

---

## P3 · Runner reconciler generic

**Mục tiêu:** Mỗi Windows runner chạy N process — mỗi `(bot_code, tenant_id)`
được gán cho nó là một process. Reconciler giữ thực tế đồng bộ với
bảng `tenant_bot_subscription`.

| Trường | Giá trị |
|---|---|
| **Owner** | Windows runner dev |
| **File** | `runner/<runner-id>/reconciler.ps1` (hoặc `reconciler.py`), `runner/<runner-id>/port_pool.json`, `runner/<runner-id>/mt5_slot_pool.json`, `WINDOWS_RUNNER_HANDOFF_runner-win-01.md` (cập nhật) |
| **Bot-agnostic?** | CÓ. Reconciler đọc assignment qua `GET /api/runner/<runner_id>/assignments`. Mỗi assignment có `bot_code, tenant_id, package_version, env_block`. Reconciler KHÔNG biết `gsalgovip` khác `botX` chỗ nào — nó chỉ download package `bot-trading/<bot_code>` đúng phiên bản, render env, spawn các process được khai trong `manifest.legacy_entrypoints`. |
| **Pool per-runner** | `port_pool` (ví dụ 8400–8499) và `mt5_slot_pool` (slot dir `E:\runner\slots\slot_001`–`slot_099`) — đều per-runner, được backend cấp lúc assignment. |
| **Bố cục process** | Một uvicorn process cho `manifest.legacy_entrypoints.fastapi_asgi`, một worker process cho `manifest.legacy_entrypoints.worker_main`. Cả hai dưới supervisor (NSSM / pywinservice). Restart policy: `on-failure, max 5/min`. |
| **Chu kỳ sync** | 30s. Reconciler diff: assignment trừ process đang chạy → start. Đang chạy trừ assignment → stop. |
| **Health check** | Sau khi spawn, poll `/healthz` trên port mới. Không healthy trong 30s → mark `status=unhealthy` trong bảng subscription (backend sẽ reschedule). |
| **Rủi ro** | Cao — Windows process management lịch sử là failure mode. Mitigate: (a) spawn idempotent (file PID per `(bot_code, tenant_id)`), (b) supervisor sở hữu lifecycle, (c) bot package theo contract KHÔNG ĐƯỢC gọi `taskkill` (đã có trong PACKAGE_STANDARD §14). |
| **Rollback** | `RUNNER_RECONCILER_ENABLED=false` → reconciler ngừng sync; process đang chạy vẫn chạy tới khi stop thủ công. |
| **Acceptance test** | (1) Gán `(gsalgovip, t-smoke)` → 2 process xuất hiện trên runner-w2 trong 60s. (2) Gán `(botX, t-smoke)` cùng runner → thêm 2 process (port khác, MT5 slot khác). (3) Hủy gán → cả hai stop trong 60s, MT5 slot trả về pool. |
| **Phụ thuộc** | P1, P2, P7 |
| **Effort** | 3 ngày |

---

## P4 · nginx routing generic

**Mục tiêu:** URL webhook public `https://api.cntx.com/t/<tenant_id>/<bot_code>/webhook/...`
được proxy tới đúng host:port của runner, không phụ thuộc bot.

| Trường | Giá trị |
|---|---|
| **Owner** | DevOps / Infra |
| **File** | `nginx.conf` (hoặc `config/nginx-spider.conf`), file map auto-generate `/etc/nginx/maps/tenant_bot.map` |
| **Bot-agnostic?** | CÓ. Pattern URL chứa `bot_code` như một path component. File map render từ bảng `tenant_bot_subscription` bằng một reconciler nhỏ (consul-template, sidecar, hoặc cron 30s). |
| **Pattern URL** | `^/t/(?<tenant>[a-zA-Z0-9_-]+)/(?<bot>[a-z][a-z0-9_]+)/webhook/(?<path>.*)$` |
| **Format file map** | `<tenant>::<bot> <runner_host>:<port>;` mỗi dòng. Build từ row `tenant_bot_subscription` có `status='running'`. |
| **Reload** | `nginx -t` rồi `systemctl reload nginx`. Graceful, không drop request đang bay. |
| **Rủi ro** | Thấp nếu map auto-generate và validate trước reload. Trung nếu sửa tay (đừng làm). |
| **Rollback** | Thay map bằng file rỗng → mọi request `/t/...` 404, nhưng path cũ (ví dụ URL single-tenant cũ của `gsalgo`) vẫn chạy. |
| **Acceptance test** | (1) `curl /t/t-smoke/gsalgovip/webhook/tradingview` → đáp ở runner-w2:port-X. (2) `curl /t/t-smoke/botX/webhook/tradingview` → đáp ở runner-w2:port-Y. (3) `curl /t/no-such-tenant/...` → 404 / 502 với body rõ ràng. |
| **Phụ thuộc** | P3 (cần host:port của runner để forward) |
| **Effort** | 1 ngày |

---

## P5 · Catalog frontend generic

**Mục tiêu:** Mini App liệt kê mọi bot trong catalog (bất kể là gì) với
*Subscribe* / *Configure* / *Stop* per `(bot, tenant)`.

| Trường | Giá trị |
|---|---|
| **Owner** | Frontend dev |
| **File** | `frontend-v2/components/Bot/BotCatalog.tsx` (mới hoặc thay component cũ), `frontend-v2/components/Bot/BotCard.tsx` (mới), `frontend-v2/lib/api.ts` (mở rộng) |
| **Bot-agnostic?** | CÓ. Frontend render từ `GET /api/bots` (catalog). Mỗi row là `{bot_code, bot_name, description, version, profile_class, strategy_tags, is_subscribed}`. Frontend không bao giờ hardcode tên bot. |
| **Form Configure** | Auto-generate từ `manifest.config_schema` (JSON Schema). Dùng `react-jsonschema-form` hoặc tương tự. Bot mới có form config miễn phí. |
| **Rủi ro** | Thấp (read-only + form generator). |
| **Rollback** | Feature flag per bot: `frontend.bot_visible[<bot_code>] = false`. |
| **Acceptance test** | Trang catalog liệt kê mọi bot từ manifest. Thêm `botX` ở backend → refresh sẽ hiện card mới. Không cần deploy FE. |
| **Phụ thuộc** | P1 |
| **Effort** | 1.5 ngày (1 ngày catalog + form, 0.5 ngày polish) |

---

## P6 · Endpoint subscribe generic

**Mục tiêu:** Một endpoint REST duy nhất xử lý subscribe cho mọi bot.

| Trường | Giá trị |
|---|---|
| **Owner** | Linux backend dev |
| **File** | `backend_ai/backend/app/api/bots.py` (mới) |
| **Endpoint** | `POST /api/bots/{bot_code}/subscribe`. Body: config tenant, validate theo `manifest.config_schema`. Response: `{webhook_url, webhook_secret}` chỉ trả về MỘT LẦN. Lần sau `GET /api/bots/{bot_code}/subscription/{tenant_id}` chỉ trả URL; rotate secret là flow riêng. |
| **Bot-agnostic?** | CÓ. Đọc manifest theo `bot_code` từ catalog. Validate body theo schema của manifest. Gọi `provisioner.provision(bot_code, tenant_id)`. Insert row vào `tenant_bot_subscription`. Reconciler (P3) sẽ pick up trong cycle kế tiếp. |
| **Bảo mật** | Chỉ HTTPS, JWT auth, audit log mỗi call. Secret không bao giờ trả lại lần thứ hai. |
| **Rủi ro** | Trung. Reveal secret phải one-shot. |
| **Rollback** | Feature flag `BOT_SUBSCRIPTION_ENABLED`. |
| **Acceptance test** | Tenant subscribe `gsalgovip` → trả URL + secret; lần gọi thứ hai chỉ trả URL. Tenant subscribe `botX` (bot khác) → URL độc lập + secret độc lập + DB độc lập. |
| **Phụ thuộc** | P2, P5, P7 |
| **Effort** | 1.5 ngày |

---

## P7 · Migration bảng subscription

**Mục tiêu:** Bảng `tenant_bot_subscription` từ §1.2.

| Trường | Giá trị |
|---|---|
| **Owner** | Linux backend dev |
| **File** | `migrations/<n>_create_tenant_bot_subscription.sql`, ORM model |
| **Bot-agnostic?** | CÓ (composite key `(tenant_id, bot_code)`). |
| **Rủi ro** | Thấp (additive). |
| **Rollback** | `DROP TABLE` + deprovision row đang active. |
| **Acceptance test** | Subscribe → row insert với `status=pending`. Reconciler (P3) flip sang `running`. Unsubscribe → soft-archive. |
| **Phụ thuộc** | không có cho migration; mọi thứ khác phụ thuộc nó |
| **Effort** | 0.5 ngày |

---

## P8 · Smoke + soak end-to-end (per bot)

**Mục tiêu:** Một harness tái dùng được, chứng minh platform xử lý được
một bot tuân thủ *mới* mà không phải sửa code.

| Trường | Giá trị |
|---|---|
| **Owner** | QA / SRE |
| **File** | `tests/e2e/test_bot_distribution.py` (mới — tham số hóa theo `bot_code`), `scripts/smoke_bot_tenant.py` (mới) |
| **Các bước** | (1) Subscribe tenant tổng hợp cho `bot_code` đích. (2) Xác nhận provisioner đã tạo DB + entry vault. (3) Xác nhận runner spawn 2 process trong 60s. (4) `curl /healthz` trả các field mong đợi. (5) POST payload tổng hợp tới URL public. (6) Xác nhận row trong DB của bot có `status=dry_run`. (7) Soak 24h với 5 tenant × 1 alert/min: zero leak MT5, zero crash. |
| **Bot-agnostic?** | CÓ — harness nhận `bot_code` qua CLI arg. Thêm `botX` = chạy `pytest -k bot_distribution --bot=botX`. |
| **Effort** | 2 ngày harness + 1 ngày soak/bot |
| **Phụ thuộc** | P1–P7 |

---

## P9 · Chính sách deprecate per bot

**Mục tiêu:** Chính sách + công cụ generic để ẩn/xóa một bot khi đã có
bản kế nhiệm.

| Trường | Giá trị |
|---|---|
| **Owner** | Linux backend dev |
| **File** | `backend_ai/backend/app/settings.py` (env: `BOT_CATALOG_DISABLED_CODES`), `tools/migrate_bot_tenants.py` (tham số: `--from-bot=gsalgo --to-bot=gsalgovip`) |
| **Chính sách** | (1) Thêm `<bot_code>` vào `BOT_CATALOG_DISABLED_CODES` → không còn subscribe mới. (2) Chạy migration tool để chuyển tenant sang bot kế nhiệm (mỗi tenant nhận URL + secret mới; tenant nhìn thấy thay đổi này). (3) Sau ≥1 tuần không có traffic vào bot cũ → an toàn `rm -rf bot-trading/<bot_code>/`. |
| **Bot-agnostic?** | CÓ. |
| **Rủi ro** | Cao nếu áp sớm (tenant còn dùng bot cũ sẽ vỡ). Chính sách cưỡng chế thứ tự. |
| **Effort** | 1 ngày toggle + tùy thời gian migration mỗi bot |

---

## 3. Risk register dùng chung

| Rủi ro | Khả năng xảy ra | Tác động | Mitigation |
|---|---|---|---|
| Bug ở 1 bot ảnh hưởng bot khác | Trung | Trung | Một process / `(bot_code, tenant_id)`. DB khác nhau. Path vault khác nhau. MT5 slot khác nhau. Không có shared mutable state runtime. |
| Va chạm port giữa các bot trên cùng runner | Trung | Cao | Pool port per-runner do backend cấp lúc assignment, không bao giờ trùng. Reconciler refuse start nếu pool cạn (raise alert). |
| Leak MT5 slot giữa các bot | Trung | Cao | Pool MT5 slot per-runner. Slot trả pool khi supervisor stop. Reconciler refuse gán nếu pool cạn. |
| Hai bot cùng tenant_id cần path vault giống nhau | Thấp | Cao | Path vault chứa `bot_code`: `secret/bots/<bot_code>/<tenant_id>/...`. Không thể trùng. |
| Catalog scan load manifest sai | Trung | Trung | Validator P1 reject manifest sai; chỉ bot tuân thủ vào bảng catalog. |
| Tác giả bot hardcode secret trong repo | Thấp | Cao | Audit `.env.example` ở CI (regex scan). Validator manifest kiểm tra `secrets_required` chỉ chứa tên, không chứa giá trị. |
| Tenant subscribe quá nhiều bot | Thấp | Trung | Quota ở endpoint subscribe: `MAX_BOTS_PER_TENANT` (env). |
| Postgres DB bùng nổ (10000s DB) | Thấp | Cao | Lâu dài: schema-per-bot trong cluster chung thay vì DB-per-tenant. Thiết kế cho v2; không block v1. |
| Phiên bản bot lệch giữa runner và catalog | Trung | Trung | Reconciler runner verify `package_version` từ assignment khớp bản local. Lệch → download lại từ source. |

---

## 4. Những thứ kế hoạch này CỐ Ý CHƯA làm

- **Quota tài nguyên per-bot.** Hiện mọi bot share cùng pool runner. v2 có
  thể thêm reserve capacity per-bot.
- **Hot reload phiên bản package bot.** Hiện tại: bump version → reconciler
  notice → restart process. Không có swap zero-downtime. Re-evaluate ở
  >50 tenant.
- **Phối hợp lệnh giữa các bot.** Nếu tenant subscribe 2 bot cùng trade
  XAUUSD, chúng không biết về nhau. Tenant phải chấp nhận. v2 có thể thêm
  "shared exposure ledger".
- **Bố cục runner đa-region.** Mọi runner giả định ở 1 DC. Schedule liên
  DC là phase tương lai.
- **Bot marketplace / bot bên thứ 3.** v1 giả định mọi bot là first-party
  (cntx_labs). Bot bên thứ 3 cần pipeline security review; v2.

---

## 5. Thêm bot mới — checklist trạng thái ổn định

Sau khi kế hoạch này được triển khai, **thêm bot mới** = 5 bước, đều
trong `bot-trading/`:

1. `cp -r bot-trading/gsalgovip bot-trading/<new_bot>`
2. Sửa `bot_manifest.json`: `bot_id`, `bot_code`, `bot_name`, `description`,
   `strategy_tags`, `default_webhook_port`, hint MT5.
3. Sửa `app/risk_guard.py`: đổi `ALLOWED_STRATEGY_NAMES` thành `<new_bot>_v1`.
4. Thay `app/worker.py` / logic strategy.
5. Bump `VERSION`.

Sau đó: **không phải sửa code platform.** Catalog loader pick up package
mới ở lần backend restart. Frontend liệt kê nó ở lần refresh page. Tenant
có thể subscribe ngay.

Nếu checklist này từng vượt 5 bước cho 1 bot mới, nghĩa là phía platform
đã trôi khỏi generic và cần refactor.

---

## 6. Sign-off checklist

Cần user duyệt từng phase. Thứ tự đề xuất:

- [ ] P1 (catalog loader generic) — nhỏ, cô lập, rủi ro thấp → unblock mọi thứ
- [ ] P7 (migration bảng subscription) — nhỏ, additive
- [ ] P2 (provisioner generic) — cần quyết định topology cluster bots-PG
- [ ] P3 (runner reconciler generic) — cần ops window cho Windows runner
- [ ] P4 (nginx routing generic) — cần ops window
- [ ] P5 (frontend catalog generic) — có thể song song sau P1
- [ ] P6 (subscribe endpoint generic) — sau P2 + P5
- [ ] P8 (e2e smoke harness) — sau tất cả phía trên
- [ ] P9 (deprecation policy generic) — sau P8 ổn định ≥1 tuần

