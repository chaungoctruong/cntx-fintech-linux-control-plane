from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.ai.training_data import AITrainingDataStore  # noqa: E402
from scripts.evaluate_ai_training_dataset import evaluate_dataset  # noqa: E402


def _safe_key(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value or "").strip())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned[:120] or f"cntx-lora-{time.strftime('%Y%m%d-%H%M%S')}"


def _default_output_dir(model_key: str) -> Path:
    return PROJECT_ROOT / "ops" / "ai" / "lora_jobs" / f"{_safe_key(model_key)}_{time.strftime('%Y%m%d_%H%M%S')}"


def _read_openai_jsonl(path: Path) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        payload = json.loads(raw)
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if not isinstance(messages, list):
            raise ValueError(f"line_{lineno}_messages_required")
        user = next((item for item in messages if isinstance(item, dict) and item.get("role") == "user"), None)
        assistant = next((item for item in messages if isinstance(item, dict) and item.get("role") == "assistant"), None)
        prompt = str((user or {}).get("content") or "").strip()
        completion = str((assistant or {}).get("content") or "").strip()
        if not prompt or not completion:
            raise ValueError(f"line_{lineno}_user_and_assistant_required")
        examples.append(
            {
                "conversations": [
                    {"from": "human", "value": prompt},
                    {"from": "gpt", "value": completion},
                ]
            }
        )
    return examples


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if not text:
        return '""'
    if any(ch in text for ch in [":", "#", "{", "}", "[", "]", ",", "&", "*", "!", "|", ">", "'", '"', "%", "@", "`"]):
        return json.dumps(text, ensure_ascii=False)
    return text


def _write_simple_yaml(path: Path, sections: list[tuple[str, dict[str, Any]]]) -> None:
    lines: list[str] = []
    for title, values in sections:
        lines.append(f"### {title}")
        for key, value in values.items():
            lines.append(f"{key}: {_yaml_scalar(value)}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def build_lora_training_job(
    *,
    dataset: Path,
    model_key: str,
    base_model: str,
    output_dir: Path | None = None,
    min_examples: int = 20,
    pass_threshold: float = 0.95,
    allow_failing_eval: bool = False,
    register: bool = False,
    store: AITrainingDataStore | None = None,
) -> dict[str, Any]:
    dataset_path = dataset.resolve()
    if not dataset_path.is_file():
        raise ValueError("dataset_file_missing")
    safe_model_key = _safe_key(model_key)
    if not str(base_model or "").strip():
        raise ValueError("base_model_required")

    eval_summary = evaluate_dataset(path=dataset_path, pass_threshold=pass_threshold)
    if not eval_summary.get("passed") and not allow_failing_eval:
        raise ValueError(f"dataset_eval_failed:{eval_summary.get('score')}")

    examples = _read_openai_jsonl(dataset_path)
    min_examples_i = max(1, int(min_examples or 1))
    if len(examples) < min_examples_i:
        raise ValueError(f"dataset_too_small:{len(examples)}<{min_examples_i}")

    job_dir = (output_dir or _default_output_dir(safe_model_key)).resolve()
    job_dir.mkdir(parents=True, exist_ok=True)
    sharegpt_path = job_dir / "dataset_sharegpt.json"
    dataset_info_path = job_dir / "dataset_info.json"
    train_yaml_path = job_dir / "llamafactory_train.yaml"
    readme_path = job_dir / "README.md"
    manifest_path = job_dir / "training_job.json"
    adapter_dir = job_dir / "adapter"

    _write_json(sharegpt_path, examples)
    dataset_info = {
        "cntx_lora_dataset": {
            "file_name": sharegpt_path.name,
            "formatting": "sharegpt",
            "columns": {"messages": "conversations"},
            "tags": {
                "role_tag": "from",
                "content_tag": "value",
                "user_tag": "human",
                "assistant_tag": "gpt",
            },
        }
    }
    _write_json(dataset_info_path, dataset_info)
    _write_simple_yaml(
        train_yaml_path,
        [
            (
                "model",
                {
                    "model_name_or_path": base_model,
                    "trust_remote_code": True,
                },
            ),
            (
                "method",
                {
                    "stage": "sft",
                    "do_train": True,
                    "finetuning_type": "lora",
                    "lora_target": "all",
                },
            ),
            (
                "dataset",
                {
                    "dataset": "cntx_lora_dataset",
                    "dataset_dir": str(job_dir),
                    "template": "qwen",
                    "cutoff_len": 2048,
                    "max_samples": len(examples),
                    "overwrite_cache": True,
                    "preprocessing_num_workers": 4,
                },
            ),
            (
                "output",
                {
                    "output_dir": str(adapter_dir),
                    "logging_steps": 10,
                    "save_steps": 100,
                    "plot_loss": True,
                    "overwrite_output_dir": True,
                },
            ),
            (
                "train",
                {
                    "per_device_train_batch_size": 1,
                    "gradient_accumulation_steps": 8,
                    "learning_rate": "2.0e-4",
                    "num_train_epochs": 3.0,
                    "lr_scheduler_type": "cosine",
                    "warmup_ratio": 0.03,
                    "bf16": True,
                    "ddp_timeout": 180000000,
                },
            ),
        ],
    )

    command = f"llamafactory-cli train {train_yaml_path}"
    readme_path.write_text(
        "\n".join(
            [
                "# CNTx LoRA Training Job",
                "",
                "This package is generated from reviewed PostgreSQL training examples.",
                "Run training on a dedicated AI/GPU machine, not inside the Linux control-plane runtime.",
                "",
                "## Command",
                "",
                "```bash",
                command,
                "```",
                "",
                "After training, register the adapter path as a candidate model version and evaluate it before promotion.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    manifest = {
        "model_key": safe_model_key,
        "base_model": str(base_model).strip(),
        "source_dataset": str(dataset_path),
        "source_dataset_sha256": _sha256_file(dataset_path),
        "job_dir": str(job_dir),
        "sharegpt_dataset": str(sharegpt_path),
        "dataset_info": str(dataset_info_path),
        "train_config": str(train_yaml_path),
        "adapter_path": str(adapter_dir),
        "example_count": len(examples),
        "eval": eval_summary,
        "trainer": "llamafactory",
        "train_command": command,
        "created_at": int(time.time()),
    }
    _write_json(manifest_path, manifest)

    if register:
        (store or AITrainingDataStore()).register_model_version_sync(
            model_key=safe_model_key,
            base_model=str(base_model).strip(),
            adapter_path=str(adapter_dir),
            dataset_export_key=dataset_path.stem,
            status="candidate",
            metrics={"dataset_static_score": eval_summary.get("score")},
            metadata={
                "training_job_manifest": str(manifest_path),
                "trainer": "llamafactory",
                "train_config": str(train_yaml_path),
            },
        )
        manifest["registered"] = True
    else:
        manifest["registered"] = False
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an offline LoRA training job package from approved CNTx JSONL.")
    parser.add_argument("--dataset", required=True, help="Approved OpenAI-chat JSONL exported by export_ai_training_dataset.py.")
    parser.add_argument("--model-key", required=True, help="Stable candidate model key, e.g. cntx-qwen-lora-001.")
    parser.add_argument("--base-model", required=True, help="Base model id/path used by the external trainer.")
    parser.add_argument("--output-dir", default="", help="Output job directory. Default uses ops/ai/lora_jobs.")
    parser.add_argument("--min-examples", type=int, default=20)
    parser.add_argument("--pass-threshold", type=float, default=0.95)
    parser.add_argument("--allow-failing-eval", action="store_true")
    parser.add_argument("--register", action="store_true", help="Register candidate adapter metadata in PostgreSQL.")
    args = parser.parse_args()

    try:
        manifest = build_lora_training_job(
            dataset=Path(args.dataset),
            model_key=args.model_key,
            base_model=args.base_model,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            min_examples=args.min_examples,
            pass_threshold=args.pass_threshold,
            allow_failing_eval=bool(args.allow_failing_eval),
            register=bool(args.register),
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps({"status": "created", **manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
