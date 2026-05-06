from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.ai.training_data import AITrainingDataStore, training_skip_reason  # noqa: E402


_LEAK_RE = re.compile(
    r"(?i)(password|passwd|token|secret|api\s*key|authorization|bearer|redis://|postgres://|postgresql://|mt5:runner:|mt5:execution:)"
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception as exc:
            rows.append({"_error": f"invalid_json:{lineno}:{exc}"})
            continue
        if isinstance(payload, dict):
            payload["_lineno"] = lineno
            rows.append(payload)
        else:
            rows.append({"_error": f"invalid_object:{lineno}"})
    return rows


def _messages_to_pair(row: dict[str, Any]) -> tuple[str, str]:
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        return "", ""
    user = next((item for item in messages if isinstance(item, dict) and item.get("role") == "user"), None)
    assistant = next((item for item in messages if isinstance(item, dict) and item.get("role") == "assistant"), None)
    return str((user or {}).get("content") or "").strip(), str((assistant or {}).get("content") or "").strip()


def evaluate_dataset(
    *,
    path: Path,
    pass_threshold: float = 0.95,
    record: bool = False,
    model_key: str = "dataset_static",
    dataset_export_key: str = "",
    store: AITrainingDataStore | None = None,
) -> dict[str, Any]:
    rows = _read_jsonl(path)
    total = len(rows)
    invalid = 0
    leak_hits = 0
    blocked_by_policy = 0
    duplicate_prompts = 0
    prompt_seen: set[str] = set()
    failures: list[dict[str, Any]] = []

    for row in rows:
        if row.get("_error"):
            invalid += 1
            failures.append({"line": row.get("_lineno"), "reason": row["_error"]})
            continue
        prompt, completion = _messages_to_pair(row)
        line = int(row.get("_lineno") or 0)
        if not prompt or not completion:
            invalid += 1
            failures.append({"line": line, "reason": "missing_user_or_assistant_message"})
            continue
        prompt_key = prompt.strip().lower()
        if prompt_key in prompt_seen:
            duplicate_prompts += 1
            failures.append({"line": line, "reason": "duplicate_prompt"})
        prompt_seen.add(prompt_key)
        if _LEAK_RE.search(prompt) or _LEAK_RE.search(completion):
            leak_hits += 1
            failures.append({"line": line, "reason": "sensitive_or_internal_pattern"})
            continue
        skip = training_skip_reason(prompt=prompt, completion=completion)
        if skip:
            blocked_by_policy += 1
            failures.append({"line": line, "reason": skip})

    hard_failures = invalid + leak_hits + blocked_by_policy + duplicate_prompts
    score = 1.0 if total <= 0 else max(0.0, 1.0 - (hard_failures / total))
    threshold = max(0.0, min(float(pass_threshold), 1.0))
    passed = score >= threshold and total > 0
    summary = {
        "dataset_path": str(path),
        "example_count": total,
        "invalid_count": invalid,
        "leak_hit_count": leak_hits,
        "policy_block_count": blocked_by_policy,
        "duplicate_prompt_count": duplicate_prompts,
        "score": round(score, 5),
        "pass_threshold": threshold,
        "passed": passed,
        "failures_sample": failures[:50],
    }

    if record:
        run_key = f"dataset_static_{path.stem}_{time.strftime('%Y%m%d_%H%M%S')}"
        (store or AITrainingDataStore()).record_eval_run_sync(
            run_key=run_key,
            model_key=model_key,
            dataset_export_key=dataset_export_key or path.stem,
            eval_type="dataset_static",
            status="completed" if passed else "failed",
            example_count=total,
            score=score,
            pass_threshold=threshold,
            metrics=summary,
            metadata={"tool": "scripts/evaluate_ai_training_dataset.py"},
        )
        summary["run_key"] = run_key
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Static safety/format evaluation for CNTx AI training JSONL.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--pass-threshold", type=float, default=0.95)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--model-key", default="dataset_static")
    parser.add_argument("--dataset-export-key", default="")
    args = parser.parse_args()

    summary = evaluate_dataset(
        path=Path(args.dataset).resolve(),
        pass_threshold=args.pass_threshold,
        record=bool(args.record),
        model_key=args.model_key,
        dataset_export_key=args.dataset_export_key,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
