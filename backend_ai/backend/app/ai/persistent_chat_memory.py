from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional

from app.services.store_service import get_process_store
from app.settings import settings
from app.ai.training_data import AITrainingDataStore

log = logging.getLogger("ai_persistent_chat_memory")

_SAFE_MODES = {"chat", "support", "sales", "market", "complaint", "retention"}
_GUEST_SCOPES = {"", "guest", "unknown", "anonymous"}
_REDACTED = "[redacted_sensitive]"


@dataclass(frozen=True)
class CachedAIAnswer:
    reply: str
    cache_id: Optional[int] = None
    hit_count: int = 0
    source: str = "db_cache"


@dataclass(frozen=True)
class LearnedAIAnswer:
    question: str
    answer: str
    score: float
    scope: str
    cache_id: Optional[int] = None


def _safe_str(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _safe_mode(value: Any) -> str:
    mode = _safe_str(value, "chat").lower()
    return mode if mode in _SAFE_MODES else "chat"


def _user_scope(user_id: Any) -> str:
    value = _safe_str(user_id)
    if value.lower() in _GUEST_SCOPES:
        return ""
    return f"user:{value[:120]}"


def _normalize_vi(text: Any) -> str:
    raw = _safe_str(text).lower()
    raw = raw.replace("đ", "d")
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = re.sub(r"[^a-z0-9\s]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def question_hash(question: Any) -> tuple[str, str]:
    normalized = _normalize_vi(question)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return normalized, digest


def _max_question_len() -> int:
    return max(32, int(getattr(settings, "AI_CHAT_DB_CACHE_MAX_QUESTION_LEN", 1000) or 1000))


def _max_answer_len() -> int:
    return max(200, int(getattr(settings, "AI_CHAT_DB_CACHE_MAX_ANSWER_LEN", 4000) or 4000))


def _max_message_len() -> int:
    return max(200, int(getattr(settings, "AI_CHAT_DB_MESSAGE_MAX_CONTENT", 4000) or 4000))


def _cache_min_updated_at(now_ts: Optional[int] = None) -> int:
    ttl_raw = int(getattr(settings, "AI_CHAT_DB_CACHE_TTL_SEC", 0) or 0)
    if ttl_raw <= 0:
        return 0
    return int(now_ts or time.time()) - max(300, ttl_raw)


def _min_question_len() -> int:
    return max(1, int(getattr(settings, "AI_CHAT_DB_CACHE_MIN_QUESTION_LEN", 4) or 4))


def _history_enabled() -> bool:
    return bool(getattr(settings, "AI_CHAT_DB_MEMORY_ENABLED", True))


def _cache_enabled() -> bool:
    return bool(getattr(settings, "AI_CHAT_DB_CACHE_ENABLED", True))


def _global_learning_enabled() -> bool:
    return bool(getattr(settings, "AI_CHAT_DB_GLOBAL_LEARNING_ENABLED", True))


def _learned_scan_limit() -> int:
    return max(10, int(getattr(settings, "AI_CHAT_DB_LEARNED_SCAN_LIMIT", 80) or 80))


def _learned_context_limit() -> int:
    return max(1, int(getattr(settings, "AI_CHAT_DB_LEARNED_CONTEXT_LIMIT", 3) or 3))


def _learned_similarity_threshold() -> float:
    return max(0.1, min(float(getattr(settings, "AI_CHAT_DB_LEARNED_SIMILARITY_THRESHOLD", 0.42) or 0.42), 1.0))


def _json_dumps(payload: dict[str, Any]) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return "{}"


def _truncate(text: Any, limit: int) -> str:
    value = _safe_str(text)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


_SENSITIVE_PATTERNS = (
    r"\b(password|passwd|pwd|token|secret|api\s*key|private\s*key|seed\s*phrase|authorization|bearer)\b",
    r"\b(mat\s*khau|mk|otp|2fa)\b",
    r"\b(redis|postgres|postgresql|mysql|mongodb)://",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
)

_RUNTIME_SENSITIVE_HINTS = (
    "cua toi",
    "tai khoan toi",
    "tai khoan cua toi",
    "account toi",
    "account cua toi",
    "bot toi",
    "bot cua toi",
    "slot toi",
    "slot cua toi",
    "deployment",
    "runner",
    "dang chay",
    "hien tai",
    "bay gio",
    "luc nay",
    "trang thai",
    "status",
    "so du",
    "balance",
    "equity",
    "margin",
    "lenh cua toi",
    "lich su lenh",
    "verify",
    "verification",
    "command",
    "start bot",
    "stop bot",
    "tat bot",
    "bat bot",
    "hom nay",
    "toi nay",
    "sang nay",
    "moi nhat",
    "cap nhat",
    "tin tuc",
    "gia vang",
    "xauusd",
    "btc",
    "bitcoin",
    "forex",
    "prompt",
    "system prompt",
    "developer message",
    "instruction",
    "model",
    "ollama",
    "gemini",
    "claude",
    "chatgpt",
    "openai",
    "llm",
    "db",
    "database",
    "postgres",
    "postgresql",
    "redis",
    "cache",
    "memory",
    "rag",
    "embedding",
    "vector",
    "endpoint",
    "api endpoint",
    "source code",
    "ma nguon",
    "file",
    "server",
    "docker",
    "nginx",
    "pm2",
    "linux backend",
    "control plane",
)

_RUNTIME_CONTEXT_KEYS = {
    "account_id",
    "deployment_id",
    "runner_id",
    "slot_id",
    "verification_job_id",
    "command_id",
    "broker_account_id",
    "mt5_login",
}


def looks_sensitive(text: Any) -> bool:
    raw = _safe_str(text)
    if not raw:
        return False
    normalized = _normalize_vi(raw)
    return any(re.search(pattern, raw, flags=re.IGNORECASE) or re.search(pattern, normalized) for pattern in _SENSITIVE_PATTERNS)


def _looks_runtime_sensitive(question: Any, *, mode: str, context: Optional[dict[str, Any]], use_search: bool) -> bool:
    if use_search or mode == "market":
        return True
    if isinstance(context, dict) and any(context.get(key) for key in _RUNTIME_CONTEXT_KEYS):
        return True
    normalized = _normalize_vi(question)
    return any(hint in normalized for hint in _RUNTIME_SENSITIVE_HINTS)


def cache_skip_reason(
    *,
    user_id: Any,
    question: Any,
    answer: Any = "",
    mode: Any = "chat",
    context: Optional[dict[str, Any]] = None,
    use_search: bool = False,
) -> Optional[str]:
    if not _cache_enabled():
        return "cache_disabled"
    if not _user_scope(user_id):
        return "guest_scope"

    normalized, _digest = question_hash(question)
    if len(normalized) < _min_question_len():
        return "question_too_short"
    if len(_safe_str(question)) > _max_question_len():
        return "question_too_long"
    if len(_safe_str(answer)) > _max_answer_len():
        return "answer_too_long"
    if looks_sensitive(question) or looks_sensitive(answer):
        return "sensitive_content"
    safe_mode = _safe_mode(mode)
    if _looks_runtime_sensitive(question, mode=safe_mode, context=context, use_search=use_search):
        return "runtime_or_dynamic_question"
    return None


def shared_cache_skip_reason(
    *,
    question: Any,
    answer: Any = "",
    mode: Any = "chat",
    context: Optional[dict[str, Any]] = None,
    use_search: bool = False,
) -> Optional[str]:
    if not _global_learning_enabled():
        return "global_learning_disabled"
    if not _cache_enabled():
        return "cache_disabled"

    normalized, _digest = question_hash(question)
    if len(normalized) < _min_question_len():
        return "question_too_short"
    if len(_safe_str(question)) > _max_question_len():
        return "question_too_long"
    if len(_safe_str(answer)) > _max_answer_len():
        return "answer_too_long"
    if looks_sensitive(question) or looks_sensitive(answer):
        return "sensitive_content"
    safe_mode = _safe_mode(mode)
    if _looks_runtime_sensitive(question, mode=safe_mode, context=context, use_search=use_search):
        return "runtime_or_dynamic_question"
    return None


def _stored_content(content: Any) -> tuple[str, bool]:
    text = _safe_str(content)
    if looks_sensitive(text):
        return _REDACTED, True
    return _truncate(text, _max_message_len()), False


def _tokens(text: Any) -> set[str]:
    return {
        token
        for token in _normalize_vi(text).split()
        if len(token) >= 2 and token not in {"la", "gi", "va", "co", "cho", "toi", "minh", "ban", "em", "anh", "chi"}
    }


def _similarity_score(query: Any, candidate: Any) -> float:
    query_tokens = _tokens(query)
    candidate_tokens = _tokens(candidate)
    if not query_tokens or not candidate_tokens:
        return 0.0
    intersection = len(query_tokens.intersection(candidate_tokens))
    if intersection <= 0:
        return 0.0
    containment = intersection / max(1, min(len(query_tokens), len(candidate_tokens)))
    jaccard = intersection / max(1, len(query_tokens.union(candidate_tokens)))
    return round(max(containment * 0.8, jaccard), 4)


class PersistentChatMemory:
    def __init__(self, store: Any = None) -> None:
        self.store = store

    def _store(self) -> Any:
        return self.store or get_process_store()

    def lookup_cached_answer_sync(
        self,
        *,
        user_id: Any,
        question: Any,
        mode: Any = "chat",
        context: Optional[dict[str, Any]] = None,
        use_search: bool = False,
    ) -> Optional[CachedAIAnswer]:
        safe_mode = _safe_mode(mode)
        user_skip = cache_skip_reason(user_id=user_id, question=question, mode=safe_mode, context=context, use_search=use_search)
        shared_skip = shared_cache_skip_reason(question=question, mode=safe_mode, context=context, use_search=use_search)
        if user_skip and shared_skip:
            return None

        normalized, digest = question_hash(question)
        now_ts = int(time.time())
        min_updated_at = _cache_min_updated_at(now_ts)
        scopes: list[str] = []
        user_scope = _user_scope(user_id)
        if not user_skip and user_scope:
            scopes.append(user_scope)
        if not shared_skip:
            scopes.append("global")
        if not scopes:
            return None

        def _do(_con: Any, cur: Any) -> Optional[CachedAIAnswer]:
            row = None
            for scope in scopes:
                cur.execute(
                    """
                    SELECT id, answer, source, hit_count
                    FROM ai_chat_answer_cache
                    WHERE scope = %s
                      AND mode = %s
                      AND question_hash = %s
                      AND normalized_question = %s
                      AND enabled = TRUE
                      AND updated_at >= %s
                    LIMIT 1
                    """,
                    (scope, safe_mode, digest, normalized, min_updated_at),
                )
                row = cur.fetchone()
                if row:
                    break
            if row is None:
                return None

            cache_id = int(row.get("id") or 0)
            cur.execute(
                """
                UPDATE ai_chat_answer_cache
                SET hit_count = hit_count + 1,
                    last_hit_at = %s
                WHERE id = %s
                """,
                (now_ts, cache_id),
            )
            return CachedAIAnswer(
                reply=_safe_str(row.get("answer")),
                cache_id=cache_id,
                hit_count=int(row.get("hit_count") or 0) + 1,
                source=_safe_str(row.get("source"), "db_cache") or "db_cache",
            )

        try:
            return self._store()._with_retry_locked(_do)
        except Exception as exc:
            log.warning("AI persistent cache lookup skipped due to DB error: %s", str(exc)[:180])
            return None

    def load_learned_answers_sync(
        self,
        *,
        user_id: Any,
        question: Any,
        mode: Any = "chat",
        context: Optional[dict[str, Any]] = None,
        use_search: bool = False,
    ) -> list[LearnedAIAnswer]:
        safe_mode = _safe_mode(mode)
        if shared_cache_skip_reason(question=question, mode=safe_mode, context=context, use_search=use_search):
            return []

        query_norm, _digest = question_hash(question)
        scopes = ["global"]
        user_scope = _user_scope(user_id)
        if user_scope and not cache_skip_reason(user_id=user_id, question=question, mode=safe_mode, context=context, use_search=use_search):
            scopes.insert(0, user_scope)

        min_updated_at = _cache_min_updated_at()

        def _do(_con: Any, cur: Any) -> list[LearnedAIAnswer]:
            cur.execute(
                """
                SELECT id, scope, sample_question, answer, normalized_question, hit_count, updated_at
                FROM ai_chat_answer_cache
                WHERE enabled = TRUE
                  AND mode = %s
                  AND updated_at >= %s
                  AND scope = ANY(%s)
                ORDER BY updated_at DESC, hit_count DESC
                LIMIT %s
                """,
                (safe_mode, min_updated_at, scopes, _learned_scan_limit()),
            )
            rows = cur.fetchall() or []
            learned: list[LearnedAIAnswer] = []
            for row in rows:
                candidate_norm = _safe_str(row.get("normalized_question"))
                score = _similarity_score(query_norm, candidate_norm)
                if score < _learned_similarity_threshold():
                    continue
                learned.append(
                    LearnedAIAnswer(
                        question=_truncate(row.get("sample_question"), _max_question_len()),
                        answer=_truncate(row.get("answer"), _max_answer_len()),
                        score=score,
                        scope="personal" if _safe_str(row.get("scope")) == user_scope else "platform",
                        cache_id=int(row.get("id") or 0),
                    )
                )
            learned.sort(key=lambda item: (item.score, item.scope == "personal"), reverse=True)
            return learned[: _learned_context_limit()]

        try:
            return self._store()._with_retry_read(_do)
        except Exception as exc:
            log.warning("AI learned memory lookup skipped due to DB error: %s", str(exc)[:180])
            return []

    def _insert_message(
        self,
        *,
        user_id: Any,
        role: str,
        content: Any,
        mode: str,
        status: str,
        source: str,
        metadata: dict[str, Any],
        created_at: int,
    ) -> None:
        if not _history_enabled() or not _user_scope(user_id):
            return

        stored, redacted = _stored_content(content)
        normalized = _normalize_vi(stored)
        content_digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        message_metadata = dict(metadata or {})
        if redacted:
            message_metadata["redacted"] = True

        def _do(_con: Any, cur: Any) -> None:
            cur.execute(
                """
                INSERT INTO ai_chat_messages(
                    user_id, role, content, content_hash, mode, status, source, metadata_json, created_at
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                """,
                (
                    _user_scope(user_id)[5:],
                    role,
                    stored,
                    content_digest,
                    mode,
                    status,
                    source,
                    _json_dumps(message_metadata),
                    created_at,
                ),
            )

        self._store()._with_retry_locked(_do)

    def _upsert_answer_cache(
        self,
        *,
        scope: str,
        question: Any,
        answer: Any,
        mode: str,
        source: str,
        metadata: dict[str, Any],
        created_at: int,
    ) -> None:
        normalized, digest = question_hash(question)

        def _do(_con: Any, cur: Any) -> None:
            cur.execute(
                """
                INSERT INTO ai_chat_answer_cache(
                    scope, mode, question_hash, normalized_question, sample_question, answer,
                    source, enabled, hit_count, metadata_json, created_at, updated_at, last_hit_at
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,TRUE,0,%s::jsonb,%s,%s,NULL)
                ON CONFLICT(scope, mode, question_hash) DO UPDATE SET
                    normalized_question = EXCLUDED.normalized_question,
                    sample_question = EXCLUDED.sample_question,
                    answer = EXCLUDED.answer,
                    source = EXCLUDED.source,
                    enabled = TRUE,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    scope,
                    mode,
                    digest,
                    normalized,
                    _truncate(question, _max_question_len()),
                    _truncate(answer, _max_answer_len()),
                    source,
                    _json_dumps(metadata),
                    created_at,
                    created_at,
                ),
            )

        self._store()._with_retry_locked(_do)

    def persist_exchange_sync(
        self,
        *,
        user_id: Any,
        user_msg: Any,
        assistant_reply: Any,
        mode: Any = "chat",
        status: Any = "done",
        source: Any = "executor",
        context: Optional[dict[str, Any]] = None,
        use_search: bool = False,
    ) -> None:
        safe_mode = _safe_mode(mode)
        safe_status = _safe_str(status, "done").lower()[:40]
        safe_source = _safe_str(source, "executor").lower()[:40]
        created_at = int(time.time())
        metadata = {
            "cache_skip_reason": cache_skip_reason(
                user_id=user_id,
                question=user_msg,
                answer=assistant_reply,
                mode=safe_mode,
                context=context,
                use_search=use_search,
            )
        }

        try:
            self._insert_message(
                user_id=user_id,
                role="user",
                content=user_msg,
                mode=safe_mode,
                status=safe_status,
                source=safe_source,
                metadata=metadata,
                created_at=created_at,
            )
            self._insert_message(
                user_id=user_id,
                role="assistant",
                content=assistant_reply,
                mode=safe_mode,
                status=safe_status,
                source=safe_source,
                metadata=metadata,
                created_at=created_at,
            )

            if safe_status == "done" and safe_source not in {"db_cache", "cache", "fallback"}:
                user_scope = _user_scope(user_id)
                if user_scope and not metadata["cache_skip_reason"]:
                    self._upsert_answer_cache(
                        scope=user_scope,
                        question=user_msg,
                        answer=assistant_reply,
                        mode=safe_mode,
                        source=safe_source,
                        metadata={**metadata, "scope_type": "personal"},
                        created_at=created_at,
                    )
                training_skip = AITrainingDataStore(store=self._store()).capture_candidate_sync(
                    user_id=user_id,
                    prompt=user_msg,
                    completion=assistant_reply,
                    mode=safe_mode,
                    source="chat",
                    source_ref="ai_chat_messages",
                    metadata={
                        "chat_source": safe_source,
                        "cache_skip_reason": metadata["cache_skip_reason"],
                    },
                    context=context,
                    use_search=use_search,
                )
                if training_skip:
                    metadata["training_skip_reason"] = training_skip
                shared_skip = shared_cache_skip_reason(
                    question=user_msg,
                    answer=assistant_reply,
                    mode=safe_mode,
                    context=context,
                    use_search=use_search,
                )
                if user_scope and not shared_skip:
                    shared_metadata = {
                        **metadata,
                        "scope_type": "platform",
                        "shared_cache_skip_reason": None,
                    }
                    self._upsert_answer_cache(
                        scope="global",
                        question=user_msg,
                        answer=assistant_reply,
                        mode=safe_mode,
                        source=safe_source,
                        metadata=shared_metadata,
                        created_at=created_at,
                    )
        except Exception as exc:
            log.warning("AI persistent memory write skipped due to DB error: %s", str(exc)[:180])


async def lookup_cached_answer(
    *,
    user_id: Any,
    question: Any,
    mode: Any = "chat",
    context: Optional[dict[str, Any]] = None,
    use_search: bool = False,
) -> Optional[CachedAIAnswer]:
    memory = PersistentChatMemory()
    return await asyncio.to_thread(
        memory.lookup_cached_answer_sync,
        user_id=user_id,
        question=question,
        mode=mode,
        context=context,
        use_search=use_search,
    )


async def load_learned_answers(
    *,
    user_id: Any,
    question: Any,
    mode: Any = "chat",
    context: Optional[dict[str, Any]] = None,
    use_search: bool = False,
) -> list[dict[str, Any]]:
    memory = PersistentChatMemory()
    rows = await asyncio.to_thread(
        memory.load_learned_answers_sync,
        user_id=user_id,
        question=question,
        mode=mode,
        context=context,
        use_search=use_search,
    )
    return [
        {
            "question": item.question,
            "answer": item.answer,
            "score": item.score,
            "scope": item.scope,
            "cache_id": item.cache_id,
        }
        for item in rows
    ]


async def persist_chat_exchange(
    *,
    user_id: Any,
    user_msg: Any,
    assistant_reply: Any,
    mode: Any = "chat",
    status: Any = "done",
    source: Any = "executor",
    context: Optional[dict[str, Any]] = None,
    use_search: bool = False,
) -> None:
    memory = PersistentChatMemory()
    await asyncio.to_thread(
        memory.persist_exchange_sync,
        user_id=user_id,
        user_msg=user_msg,
        assistant_reply=assistant_reply,
        mode=mode,
        status=status,
        source=source,
        context=context,
        use_search=use_search,
    )
