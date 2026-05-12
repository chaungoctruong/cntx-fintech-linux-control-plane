# AI training pipeline — `ops/ai/training/`

Tách **cập nhật tri thức online** khỏi **huấn luyện model** (LoRA). Backend ghi ví dụ / export / đánh giá; **train trên GPU** chạy máy riêng, không chạy trong process API production.

## Luồng (chuẩn)

1. Chat an toàn → ví dụ pending trong Postgres.
2. Ops review → approve chất lượng.
3. Export JSONL (OpenAI chat format).
4. Evaluate (safety, leak, duplicate, format).
5. `build_lora_training_job.py` → gói job.
6. Chạy `llamafactory-cli train ...` trên máy train.
7. `register_ai_model_version.py` → đăng ký bản candidate trước khi promote.

## Lệnh mẫu (cwd: **root monorepo** `linux-root-backend-hubot-v1/`)

> Đường dẫn Python: `backend_ai/backend/scripts/...`

```bash
python backend_ai/backend/scripts/review_ai_training_examples.py --ids 1 --status approved --quality-score 0.9 --reviewer-id ops
python backend_ai/backend/scripts/export_ai_training_dataset.py --min-quality 0.8
python backend_ai/backend/scripts/evaluate_ai_training_dataset.py \
  --dataset backend_ai/backend/ops/ai/training_exports/example.jsonl --record
python backend_ai/backend/scripts/build_lora_training_job.py \
  --dataset backend_ai/backend/ops/ai/training_exports/example.jsonl \
  --model-key cntx-qwen-lora-001 \
  --base-model Qwen/Qwen2.5-7B-Instruct \
  --register
```

## Quy tắc

- **Không** train model trong tiến trình control-plane Linux.
- Export chứa PII → xử lý theo policy nội bộ; không commit dataset vào git.

## Đào tạo

- Đọc thêm [scripts/README.md](../../scripts/README.md) (nhóm AI) và [ai_knowledge/README.md](../../ai_knowledge/README.md).
