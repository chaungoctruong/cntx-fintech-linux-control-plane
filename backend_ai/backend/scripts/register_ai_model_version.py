from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.ai.training_data import AITrainingDataStore  # noqa: E402


def _load_json(path_value: str) -> dict:
    if not path_value:
        return {}
    path = Path(path_value).resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Register a trained CNTx AI model/LoRA adapter version.")
    parser.add_argument("--model-key", required=True)
    parser.add_argument("--base-model", required=True)
    parser.add_argument("--adapter-path", default="")
    parser.add_argument("--dataset-export-key", default="")
    parser.add_argument("--status", default="candidate", choices=["candidate", "staging", "active", "retired", "failed"])
    parser.add_argument("--metrics-json", default="")
    parser.add_argument("--metadata-json", default="")
    args = parser.parse_args()

    metrics = _load_json(args.metrics_json)
    metadata = _load_json(args.metadata_json)
    AITrainingDataStore().register_model_version_sync(
        model_key=args.model_key,
        base_model=args.base_model,
        adapter_path=args.adapter_path,
        dataset_export_key=args.dataset_export_key,
        status=args.status,
        metrics=metrics,
        metadata=metadata,
    )
    print(
        json.dumps(
            {
                "registered": True,
                "model_key": args.model_key,
                "base_model": args.base_model,
                "status": args.status,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
