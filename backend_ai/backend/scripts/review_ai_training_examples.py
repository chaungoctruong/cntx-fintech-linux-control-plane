from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.ai.training_data import AITrainingDataStore  # noqa: E402


def _parse_ids(value: str) -> list[int]:
    ids: list[int] = []
    for item in str(value or "").replace(",", " ").split():
        if item.strip():
            ids.append(int(item))
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description="Approve/reject reviewed CNTx AI training examples.")
    parser.add_argument("--ids", required=True, help="Example ids separated by comma or space.")
    parser.add_argument("--status", required=True, choices=["approved", "rejected"])
    parser.add_argument("--reviewer-id", default="ops")
    parser.add_argument("--quality-score", type=float, default=None)
    parser.add_argument("--note", default="")
    args = parser.parse_args()

    changed = AITrainingDataStore().review_examples_sync(
        example_ids=_parse_ids(args.ids),
        status=args.status,
        reviewer_id=args.reviewer_id,
        quality_score=args.quality_score,
        note=args.note,
    )
    print(json.dumps({"changed": changed, "status": args.status}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
