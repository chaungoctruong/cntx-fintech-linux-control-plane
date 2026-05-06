from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.ai.embedding_provider import embed_text_sync, embedding_enabled, embedding_model_name  # noqa: E402
from app.ai.platform_knowledge import PlatformKnowledgeStore  # noqa: E402
from app.services.store_service import get_process_store  # noqa: E402


def backfill_embeddings(*, limit: int, dry_run: bool = False) -> dict:
    if not embedding_enabled():
        return {
            "status": "disabled",
            "processed": 0,
            "embedded": 0,
            "failed": 0,
            "hint": "set AI_PLATFORM_KNOWLEDGE_VECTOR_ENABLED=true first",
        }

    model_name = embedding_model_name()
    store = get_process_store()
    knowledge = PlatformKnowledgeStore(store=store)
    limit = max(1, min(int(limit), 1000))

    def _load(_con, cur):
        cur.execute(
            """
            SELECT source_key, content_hash, content
            FROM ai_platform_knowledge_chunks
            WHERE enabled = TRUE
              AND (
                    embedding_json IS NULL
                    OR COALESCE(embedding_model, '') <> %s
                  )
            ORDER BY updated_at DESC, id DESC
            LIMIT %s
            """,
            (model_name, limit),
        )
        return [dict(row) for row in cur.fetchall() or []]

    rows = store._with_retry_read(_load)
    summary = {
        "status": "dry_run" if dry_run else "ok",
        "model": model_name,
        "processed": len(rows),
        "embedded": 0,
        "failed": 0,
        "errors": [],
    }
    if dry_run:
        return summary

    now_ts = int(time.time())
    for row in rows:
        try:
            embedding = embed_text_sync(str(row.get("content") or ""))
            if not embedding:
                raise RuntimeError("embedding_unavailable")
            knowledge._store_embedding_sync(
                source_key=str(row.get("source_key") or ""),
                content_hash=str(row.get("content_hash") or ""),
                embedding=embedding,
                now_ts=now_ts,
            )
            summary["embedded"] += 1
        except Exception as exc:
            summary["failed"] += 1
            if len(summary["errors"]) < 10:
                summary["errors"].append(str(exc)[:180])

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill semantic embeddings for AI platform knowledge chunks.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    summary = backfill_embeddings(limit=args.limit, dry_run=bool(args.dry_run))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if int(summary.get("failed") or 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
