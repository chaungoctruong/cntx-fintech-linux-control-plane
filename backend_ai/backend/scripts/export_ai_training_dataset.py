from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.ai.training_data import AITrainingDataStore  # noqa: E402
from app.settings import settings  # noqa: E402


def _default_output_path() -> Path:
    export_dir = Path(settings.AI_TRAINING_EXPORT_DIR or "ops/ai/training_exports")
    if not export_dir.is_absolute():
        export_dir = PROJECT_ROOT / export_dir
    return export_dir / f"cntx_ai_sft_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"


def _jsonl_line(prompt: str, completion: str) -> str:
    return json.dumps(
        {
            "messages": [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": completion},
            ]
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def export_dataset(
    *,
    output: Path | None = None,
    mode: str = "",
    limit: int = 1000,
    min_quality: float = 0.0,
    mark_exported: bool = False,
    dry_run: bool = False,
    store: AITrainingDataStore | None = None,
) -> dict:
    data_store = store or AITrainingDataStore()
    examples = data_store.load_exportable_examples_sync(mode=mode, limit=limit, min_quality=min_quality)
    output_path = output or _default_output_path()
    export_key = output_path.stem

    sha = hashlib.sha256()
    lines: list[str] = []
    for example in examples:
        line = _jsonl_line(example.prompt, example.completion)
        lines.append(line)
        sha.update(line.encode("utf-8"))
        sha.update(b"\n")

    checksum = sha.hexdigest()
    summary = {
        "export_key": export_key,
        "output_path": str(output_path),
        "example_count": len(examples),
        "checksum": checksum,
        "mode": mode or "all",
        "min_quality": min_quality,
        "mark_exported": bool(mark_exported),
        "dry_run": bool(dry_run),
    }

    if dry_run:
        return summary
    if not examples:
        return {**summary, "status": "empty"}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest_path = output_path.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    data_store.record_export_sync(
        export_key=export_key,
        output_path=str(output_path),
        checksum=checksum,
        example_ids=[example.id for example in examples],
        metadata=summary,
        mark_exported=mark_exported,
    )
    return {**summary, "status": "created", "manifest_path": str(manifest_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export approved CNTx AI training examples as chat JSONL.")
    parser.add_argument("--output", default="", help="Output JSONL path. Default uses AI_TRAINING_EXPORT_DIR.")
    parser.add_argument("--mode", default="", help="Optional mode filter: chat/support/sales/complaint/retention.")
    parser.add_argument("--limit", type=int, default=int(settings.AI_TRAINING_DEFAULT_EXPORT_LIMIT or 1000))
    parser.add_argument("--min-quality", type=float, default=0.0)
    parser.add_argument("--mark-exported", action="store_true", help="Mark approved examples as exported after writing.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    summary = export_dataset(
        output=Path(args.output).resolve() if args.output else None,
        mode=args.mode,
        limit=args.limit,
        min_quality=args.min_quality,
        mark_exported=bool(args.mark_exported),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("status") != "empty" else 2


if __name__ == "__main__":
    raise SystemExit(main())
