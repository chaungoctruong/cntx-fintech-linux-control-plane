# `scripts/` — Lệnh vận hành, smoke, AI (không phải request API)

Python/shell chạy **ngoài** vòng đời request HTTP thông thường: entry PM2, consumer nền, smoke TradingView, pipeline AI, one-off (cẩn thận).

## Chạy script như thế nào?

- **Trong Docker:** `docker compose exec spider-app bash -lc 'cd /app/backend_ai/backend && python scripts/<file>.py ...'`
- **Trên host có venv:** `cd backend_ai/backend && ./venv/bin/python scripts/<file>.py ...`
- Luôn xác nhận **`APP_ENV` / DB URL** trước khi ghi dữ liệu.

## Danh mục file (inventory)

### API / backend process

| Script | Mục đích |
|--------|----------|
| **`run_api.py`** | Entry Uvicorn/PM2 — khởi động app (xem `ecosystem.config.js` monorepo). |

*(Chỉ liệt kê file có trong thư mục `scripts/` — không có shell khởi động cụm riêng trong repo này.)*

### Runner / control-plane

| Script | Mục đích |
|--------|----------|
| **`run_runner_event_consumer.py`** | Consumer xử lý stream event runner (tiến trình nền). |
| **`run_mt5_runner_stub.py`** | Stub Linux: register + heartbeat + **dequeue Redis** (test tích hợp, không phải MT5 thật). |
| **`measure_command_latency.py`** | Đo độ trễ publish/command (dev/benchmark). |

### TradingView / broadcast

| Script | Mục đích |
|--------|----------|
| **`setup_tradingview_signal.py`** | Gắn subscription signal ↔ account, kiểm tra fan-out. |
| **`smoke_tradingview_webhook.py`** | Gửi thử webhook broadcast (cần secret/env đúng). |

### AI / knowledge

| Script | Mục đích |
|--------|----------|
| **`ingest_platform_knowledge.py`** | Nạp knowledge vào store nội bộ. |
| **`ingest_platform_sources.py`** | Nạp nguồn thô. |
| **`backfill_platform_knowledge_embeddings.py`** | Backfill embedding. |
| **`export_ai_training_dataset.py`** | Xuất JSONL training. |
| **`review_ai_training_examples.py`** | Duyệt ví dụ (approve/reject). |
| **`evaluate_ai_training_dataset.py`** | Đánh giá dataset export. |
| **`build_lora_training_job.py`** | Sinh gói job LoRA (train ngoài máy GPU). |
| **`register_ai_model_version.py`** | Đăng ký phiên bản model sau train. |

### DB / index

| Script | Mục đích |
|--------|----------|
| **`apply_control_plane_scale_indexes.py`** | Áp index scale cho control-plane (chạy cửa sổ bảo trì). |

### Ops / hạ tầng khác

| Script | Mục đích |
|--------|----------|
| **`ops/zingserver_probe.py`** | Probe ZingServer API. |
| **`ops/zingserver_plan_create_vps.py`** | Tạo plan/VPS (vận hành — đọc kỹ). |

### ⚠️ Nguy hiểm / one-off (chỉ khi có ticket rõ ràng)

| Script | Ghi chú |
|--------|---------|
| **`bulk_login_start_bot.py`** | Khối lượng lớn — chỉ chạy khi có kế hoạch + rollback. |
| **`_oneoff_place_order_*.py`** | Script tạm theo ticket; không dùng như công cụ chung. |

## Quy tắc an toàn

- Không chạy script ghi **production** khi chưa backup / chưa staging.
- Không hard-code secret; đọc từ env.
- Ghi lại lệnh đã chạy + output (audit).

## Đào tạo

1. Tuần 1: `run_api.py`, `run_runner_event_consumer.py`, smoke read-only.
2. Tuần 2: TradingView scripts trên dev.
3. Tuần 3: pipeline AI export/evaluate trên staging.
4. Tuần 4: tham gia chạy script prod có người giám sát + hậu kiểm.
