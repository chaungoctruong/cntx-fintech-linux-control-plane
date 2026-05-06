# CNTx AI Training Pipeline

This pipeline keeps online knowledge updates separate from model training.

## Flow

1. Chat answers are captured as pending examples in PostgreSQL when they pass safety filters.
2. An operator reviews pending examples and marks only correct answers as approved.
3. Approved examples are exported as OpenAI-chat JSONL.
4. The exported dataset is evaluated for basic safety, leaks, duplicates, and format errors.
5. A LoRA training job package is generated for an external AI/GPU machine.
6. The trained adapter is registered as a candidate model version before any promotion.

## Commands

List pending examples:

```bash
python backend_ai/backend/scripts/review_ai_training_examples.py --ids 1 --status approved --quality-score 0.9 --reviewer-id ops
```

Export approved examples:

```bash
python backend_ai/backend/scripts/export_ai_training_dataset.py --min-quality 0.8
```

Evaluate an export:

```bash
python backend_ai/backend/scripts/evaluate_ai_training_dataset.py --dataset ops/ai/training_exports/example.jsonl --record
```

Build a LoRA job package:

```bash
python backend_ai/backend/scripts/build_lora_training_job.py \
  --dataset ops/ai/training_exports/example.jsonl \
  --model-key cntx-qwen-lora-001 \
  --base-model Qwen/Qwen2.5-7B-Instruct \
  --register
```

Run the generated `llamafactory-cli train ...` command on a dedicated training machine.

## Runtime Rule

Do not train models inside the Linux control-plane process. The backend records data,
exports datasets, tracks candidate adapters, and continues using PostgreSQL RAG for
hourly knowledge refresh.
