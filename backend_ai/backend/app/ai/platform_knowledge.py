from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional

from app.services.store_service import get_process_store
from app.settings import settings
from app.ai.embedding_provider import embed_text_sync, embedding_model_name

log = logging.getLogger("ai_platform_knowledge")


@dataclass(frozen=True)
class PlatformKnowledgeChunk:
    source_key: str
    title: str
    content: str
    score: float
    trust_level: int = 50
    url: str = ""
    source_type: str = "manual"


def _safe_str(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _normalize_text(text: Any) -> str:
    raw = _safe_str(text).lower()
    raw = raw.replace("đ", "d")
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = re.sub(r"[^a-z0-9\s]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


_STOPWORDS = {
    "la",
    "gi",
    "va",
    "co",
    "cho",
    "toi",
    "minh",
    "ban",
    "em",
    "anh",
    "chi",
    "mot",
    "cac",
    "nhung",
    "the",
    "nao",
    "sao",
}


def _tokens(text: Any) -> set[str]:
    return {token for token in _normalize_text(text).split() if len(token) >= 2 and token not in _STOPWORDS}


def _score(query: Any, candidate: Any) -> float:
    query_tokens = _tokens(query)
    candidate_tokens = _tokens(candidate)
    if not query_tokens or not candidate_tokens:
        return 0.0
    intersection = len(query_tokens.intersection(candidate_tokens))
    if intersection <= 0:
        return 0.0
    containment = intersection / max(1, min(len(query_tokens), len(candidate_tokens)))
    jaccard = intersection / max(1, len(query_tokens.union(candidate_tokens)))
    return round(max(containment * 0.85, jaccard), 4)


def _json_dumps(payload: dict[str, Any]) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return "{}"


def _chunk_hash(content: Any) -> str:
    normalized = _normalize_text(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _enabled() -> bool:
    return bool(getattr(settings, "AI_PLATFORM_KNOWLEDGE_ENABLED", True))


def _max_chunks() -> int:
    return max(1, int(getattr(settings, "AI_PLATFORM_KNOWLEDGE_MAX_CHUNKS", 4) or 4))


def _scan_limit() -> int:
    return max(20, int(getattr(settings, "AI_PLATFORM_KNOWLEDGE_SCAN_LIMIT", 120) or 120))


def _threshold() -> float:
    return max(0.1, min(float(getattr(settings, "AI_PLATFORM_KNOWLEDGE_SIMILARITY_THRESHOLD", 0.38) or 0.38), 1.0))


def _max_chunk_chars() -> int:
    return max(400, int(getattr(settings, "AI_PLATFORM_KNOWLEDGE_CHUNK_MAX_CHARS", 1800) or 1800))


def _context_chars() -> int:
    return max(600, int(getattr(settings, "AI_PLATFORM_KNOWLEDGE_CONTEXT_CHARS", 2800) or 2800))


def _min_trust_level() -> int:
    return max(0, min(int(getattr(settings, "AI_PLATFORM_KNOWLEDGE_MIN_TRUST_LEVEL", 40) or 40), 100))


def _vector_enabled() -> bool:
    return bool(getattr(settings, "AI_PLATFORM_KNOWLEDGE_VECTOR_ENABLED", False))


def _vector_top_k() -> int:
    return max(1, int(getattr(settings, "AI_PLATFORM_KNOWLEDGE_VECTOR_TOP_K", 8) or 8))


def _vector_scan_limit() -> int:
    return max(_scan_limit(), int(getattr(settings, "AI_PLATFORM_KNOWLEDGE_VECTOR_SCAN_LIMIT", 240) or 240))


def _vector_min_score() -> float:
    return max(0.0, min(float(getattr(settings, "AI_PLATFORM_KNOWLEDGE_VECTOR_MIN_SCORE", 0.45) or 0.45), 1.0))


def _pgvector_dim() -> int:
    return max(1, int(getattr(settings, "AI_PLATFORM_KNOWLEDGE_PGVECTOR_DIM", 1024) or 1024))


def _embedding_json_column_available(cur: Any) -> bool:
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'ai_platform_knowledge_chunks'
              AND column_name = 'embedding_json'
        ) AS exists
        """
    )
    row = cur.fetchone() or {}
    return bool(row.get("exists"))


def _pgvector_column_available(cur: Any) -> bool:
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'ai_platform_knowledge_chunks'
              AND column_name = 'embedding'
        ) AS exists
        """
    )
    row = cur.fetchone() or {}
    return bool(row.get("exists"))


def _vector_literal(values: list[float]) -> str:
    safe: list[str] = []
    for item in values:
        number = float(item)
        if not math.isfinite(number):
            raise ValueError("invalid_embedding_vector")
        safe.append(f"{number:.8f}".rstrip("0").rstrip(".") or "0")
    if not safe:
        raise ValueError("empty_embedding_vector")
    return "[" + ",".join(safe) + "]"


def _parse_embedding_json(value: Any) -> list[float]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    if not isinstance(value, list):
        return []
    out: list[float] = []
    for item in value:
        try:
            number = float(item)
        except Exception:
            return []
        if not math.isfinite(number):
            return []
        out.append(number)
    return out


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right):
        dot += float(a) * float(b)
        left_norm += float(a) * float(a)
        right_norm += float(b) * float(b)
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return max(0.0, min(dot / math.sqrt(left_norm * right_norm), 1.0))


class PlatformKnowledgeStore:
    def __init__(self, store: Any = None) -> None:
        self.store = store

    def _store(self) -> Any:
        return self.store or get_process_store()

    def upsert_chunk_sync(
        self,
        *,
        source_key: str,
        source_type: str,
        title: str,
        content: str,
        url: str = "",
        trust_level: int = 50,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        safe_source_key = _safe_str(source_key)[:180]
        safe_title = _safe_str(title)[:300] or safe_source_key
        safe_content = _safe_str(content)[: _max_chunk_chars()]
        if not safe_source_key or not safe_content:
            raise ValueError("invalid_platform_knowledge_chunk")

        safe_source_type = _safe_str(source_type, "manual")[:40]
        safe_url = _safe_str(url)[:1000]
        safe_trust = max(0, min(int(trust_level), 100))
        now_ts = int(time.time())
        digest = _chunk_hash(safe_content)
        normalized = _normalize_text(safe_content)
        metadata_json = _json_dumps(dict(metadata or {}))
        embedding = embed_text_sync(safe_content) if _vector_enabled() else None

        def _do(_con: Any, cur: Any) -> None:
            cur.execute(
                """
                INSERT INTO ai_platform_knowledge_sources(
                    source_key, source_type, title, url, trust_level, enabled,
                    metadata_json, created_at, updated_at, last_ingested_at
                )
                VALUES(%s,%s,%s,%s,%s,TRUE,%s::jsonb,%s,%s,%s)
                ON CONFLICT(source_key) DO UPDATE SET
                    source_type = EXCLUDED.source_type,
                    title = EXCLUDED.title,
                    url = EXCLUDED.url,
                    trust_level = EXCLUDED.trust_level,
                    enabled = TRUE,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = EXCLUDED.updated_at,
                    last_ingested_at = EXCLUDED.last_ingested_at
                """,
                (
                    safe_source_key,
                    safe_source_type,
                    safe_title,
                    safe_url,
                    safe_trust,
                    metadata_json,
                    now_ts,
                    now_ts,
                    now_ts,
                ),
            )
            cur.execute(
                """
                INSERT INTO ai_platform_knowledge_chunks(
                    source_key, content_hash, title, url, content, normalized_content,
                    trust_level, enabled, metadata_json, created_at, updated_at
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,TRUE,%s::jsonb,%s,%s)
                ON CONFLICT(source_key, content_hash) DO UPDATE SET
                    title = EXCLUDED.title,
                    url = EXCLUDED.url,
                    content = EXCLUDED.content,
                    normalized_content = EXCLUDED.normalized_content,
                    trust_level = EXCLUDED.trust_level,
                    enabled = TRUE,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    safe_source_key,
                    digest,
                    safe_title,
                    safe_url,
                    safe_content,
                    normalized,
                    safe_trust,
                    metadata_json,
                    now_ts,
                    now_ts,
                ),
            )

        self._store()._with_retry_locked(_do)
        if embedding:
            self._store_embedding_sync(
                source_key=safe_source_key,
                content_hash=digest,
                embedding=embedding,
                now_ts=now_ts,
            )
        return digest

    def _store_embedding_sync(
        self,
        *,
        source_key: str,
        content_hash: str,
        embedding: list[float],
        now_ts: int,
    ) -> None:
        model_name = embedding_model_name()
        dim = len(embedding)
        if dim <= 0:
            return
        embedding_json = json.dumps([round(float(item), 8) for item in embedding], separators=(",", ":"))

        def _store_json(_con: Any, cur: Any) -> None:
            if not _embedding_json_column_available(cur):
                return
            cur.execute(
                """
                UPDATE ai_platform_knowledge_chunks
                SET embedding_json = %s::jsonb,
                    embedding_model = %s,
                    embedding_dim = %s,
                    embedding_updated_at = %s
                WHERE source_key = %s
                  AND content_hash = %s
                """,
                (embedding_json, model_name, dim, now_ts, source_key, content_hash),
            )

        try:
            self._store()._with_retry_locked(_store_json)
        except Exception as exc:
            log.warning("AI platform knowledge JSON embedding store skipped: %s", str(exc)[:180])
            return

        def _store_pgvector(_con: Any, cur: Any) -> None:
            if dim != _pgvector_dim():
                return
            if not _pgvector_column_available(cur):
                return
            cur.execute(
                """
                UPDATE ai_platform_knowledge_chunks
                SET embedding = %s::vector
                WHERE source_key = %s
                  AND content_hash = %s
                """,
                (_vector_literal(embedding), source_key, content_hash),
            )

        try:
            self._store()._with_retry_locked(_store_pgvector)
        except Exception as exc:
            log.warning("AI platform knowledge pgvector store skipped: %s", str(exc)[:180])

    def load_relevant_chunks_sync(
        self,
        *,
        query: str,
        intent: str = "",
        keywords: Optional[list[str]] = None,
    ) -> list[PlatformKnowledgeChunk]:
        if not _enabled():
            return []

        query_text = " ".join([_safe_str(query), _safe_str(intent), " ".join(keywords or [])]).strip()
        if not _tokens(query_text):
            return []
        if _vector_enabled():
            vector_chunks = self._load_vector_relevant_chunks_sync(query_text)
            if vector_chunks:
                return vector_chunks[: _max_chunks()]

        return self._load_keyword_relevant_chunks_sync(query_text)

    def _load_keyword_relevant_chunks_sync(self, query_text: str) -> list[PlatformKnowledgeChunk]:
        min_trust = _min_trust_level()

        def _do(_con: Any, cur: Any) -> list[PlatformKnowledgeChunk]:
            cur.execute(
                """
                SELECT
                    c.source_key,
                    c.title,
                    c.url,
                    c.content,
                    c.normalized_content,
                    c.trust_level,
                    c.updated_at,
                    COALESCE(s.source_type, 'manual') AS source_type
                FROM ai_platform_knowledge_chunks c
                LEFT JOIN ai_platform_knowledge_sources s ON s.source_key = c.source_key
                WHERE c.enabled = TRUE
                  AND c.trust_level >= %s
                  AND COALESCE(s.enabled, TRUE) = TRUE
                ORDER BY c.trust_level DESC, c.updated_at DESC, c.id DESC
                LIMIT %s
                """,
                (min_trust, _scan_limit()),
            )
            rows = cur.fetchall() or []
            chunks: list[PlatformKnowledgeChunk] = []
            for row in rows:
                score = _score(query_text, row.get("normalized_content") or row.get("content"))
                if score < _threshold():
                    continue
                chunks.append(
                    PlatformKnowledgeChunk(
                        source_key=_safe_str(row.get("source_key")),
                        title=_safe_str(row.get("title")),
                        url=_safe_str(row.get("url")),
                        content=_safe_str(row.get("content"))[: _max_chunk_chars()],
                        trust_level=int(row.get("trust_level") or 0),
                        source_type=_safe_str(row.get("source_type"), "manual"),
                        score=score,
                    )
                )
            chunks.sort(key=lambda item: (item.score, item.trust_level), reverse=True)
            return chunks[: _max_chunks()]

        try:
            return self._store()._with_retry_read(_do)
        except Exception as exc:
            log.warning("AI platform knowledge lookup skipped due to DB error: %s", str(exc)[:180])
            return []

    def _load_vector_relevant_chunks_sync(self, query_text: str) -> list[PlatformKnowledgeChunk]:
        query_embedding = embed_text_sync(query_text)
        if not query_embedding:
            return []

        pgvector_chunks = self._load_pgvector_chunks_sync(query_embedding)
        if pgvector_chunks:
            return pgvector_chunks
        return self._load_json_vector_chunks_sync(query_embedding)

    def _load_pgvector_chunks_sync(self, query_embedding: list[float]) -> list[PlatformKnowledgeChunk]:
        if len(query_embedding) != _pgvector_dim():
            return []
        min_trust = _min_trust_level()
        model_name = embedding_model_name()
        query_vector = _vector_literal(query_embedding)

        def _do(_con: Any, cur: Any) -> list[PlatformKnowledgeChunk]:
            if not _pgvector_column_available(cur):
                return []
            cur.execute(
                """
                SELECT
                    c.source_key,
                    c.title,
                    c.url,
                    c.content,
                    c.trust_level,
                    COALESCE(s.source_type, 'manual') AS source_type,
                    (c.embedding <=> %s::vector) AS distance
                FROM ai_platform_knowledge_chunks c
                LEFT JOIN ai_platform_knowledge_sources s ON s.source_key = c.source_key
                WHERE c.enabled = TRUE
                  AND c.trust_level >= %s
                  AND COALESCE(s.enabled, TRUE) = TRUE
                  AND c.embedding IS NOT NULL
                  AND c.embedding_model = %s
                ORDER BY c.embedding <=> %s::vector
                LIMIT %s
                """,
                (query_vector, min_trust, model_name, query_vector, _vector_top_k()),
            )
            chunks: list[PlatformKnowledgeChunk] = []
            for row in cur.fetchall() or []:
                distance = float(row.get("distance") or 1.0)
                score = round(max(0.0, 1.0 - distance), 4)
                if score < _vector_min_score():
                    continue
                chunks.append(
                    PlatformKnowledgeChunk(
                        source_key=_safe_str(row.get("source_key")),
                        title=_safe_str(row.get("title")),
                        url=_safe_str(row.get("url")),
                        content=_safe_str(row.get("content"))[: _max_chunk_chars()],
                        trust_level=int(row.get("trust_level") or 0),
                        source_type=_safe_str(row.get("source_type"), "manual"),
                        score=score,
                    )
                )
            return chunks

        try:
            return self._store()._with_retry_read(_do)
        except Exception as exc:
            log.warning("AI platform knowledge pgvector lookup skipped: %s", str(exc)[:180])
            return []

    def _load_json_vector_chunks_sync(self, query_embedding: list[float]) -> list[PlatformKnowledgeChunk]:
        min_trust = _min_trust_level()
        model_name = embedding_model_name()

        def _do(_con: Any, cur: Any) -> list[PlatformKnowledgeChunk]:
            if not _embedding_json_column_available(cur):
                return []
            cur.execute(
                """
                SELECT
                    c.source_key,
                    c.title,
                    c.url,
                    c.content,
                    c.embedding_json,
                    c.trust_level,
                    c.updated_at,
                    COALESCE(s.source_type, 'manual') AS source_type
                FROM ai_platform_knowledge_chunks c
                LEFT JOIN ai_platform_knowledge_sources s ON s.source_key = c.source_key
                WHERE c.enabled = TRUE
                  AND c.trust_level >= %s
                  AND COALESCE(s.enabled, TRUE) = TRUE
                  AND c.embedding_json IS NOT NULL
                  AND c.embedding_model = %s
                ORDER BY c.trust_level DESC, c.embedding_updated_at DESC NULLS LAST, c.updated_at DESC, c.id DESC
                LIMIT %s
                """,
                (min_trust, model_name, _vector_scan_limit()),
            )
            chunks: list[PlatformKnowledgeChunk] = []
            for row in cur.fetchall() or []:
                score = round(_cosine_similarity(query_embedding, _parse_embedding_json(row.get("embedding_json"))), 4)
                if score < _vector_min_score():
                    continue
                chunks.append(
                    PlatformKnowledgeChunk(
                        source_key=_safe_str(row.get("source_key")),
                        title=_safe_str(row.get("title")),
                        url=_safe_str(row.get("url")),
                        content=_safe_str(row.get("content"))[: _max_chunk_chars()],
                        trust_level=int(row.get("trust_level") or 0),
                        source_type=_safe_str(row.get("source_type"), "manual"),
                        score=score,
                    )
                )
            chunks.sort(key=lambda item: (item.score, item.trust_level), reverse=True)
            return chunks[: _vector_top_k()]

        try:
            return self._store()._with_retry_read(_do)
        except Exception as exc:
            log.warning("AI platform knowledge JSON vector lookup skipped: %s", str(exc)[:180])
            return []


def render_platform_knowledge_context(chunks: list[PlatformKnowledgeChunk]) -> str:
    if not chunks:
        return "none"
    rows: list[str] = []
    used_chars = 0
    for idx, chunk in enumerate(chunks, start=1):
        title = chunk.title or chunk.source_key or f"source_{idx}"
        source = f"{title}"
        if chunk.url:
            source += f" | {chunk.url}"
        body = chunk.content.strip()
        remaining = _context_chars() - used_chars
        if remaining <= 0:
            break
        body = body[:remaining].strip()
        used_chars += len(body)
        rows.append(
            f"[platform_knowledge:{idx} score={chunk.score:.2f} trust={chunk.trust_level}]\n"
            f"source_type={chunk.source_type}\n"
            f"source={source}\n"
            f"{body}"
        )
    return "\n\n".join(rows) if rows else "none"


async def load_platform_knowledge_context(
    *,
    query: str,
    intent: str = "",
    keywords: Optional[list[str]] = None,
) -> str:
    store = PlatformKnowledgeStore()
    chunks = await asyncio.to_thread(
        store.load_relevant_chunks_sync,
        query=query,
        intent=intent,
        keywords=keywords or [],
    )
    return render_platform_knowledge_context(chunks)
