from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.services.store_service import get_process_store
from app.settings import settings

log = logging.getLogger("ai_training_data")

_REDACTED = "[redacted_sensitive]"
_SAFE_MODES = {"chat", "support", "sales", "market", "complaint", "retention"}
_SAFE_STATUSES = {"pending", "approved", "rejected", "exported", "trained"}
_SAFE_SOURCES = {"chat", "cache", "platform_knowledge", "manual", "import"}


@dataclass(frozen=True)
class TrainingExample:
    id: int
    prompt: str
    completion: str
    mode: str
    source: str
    quality_score: float


@dataclass(frozen=True)
class TrainingExamplePreview:
    id: int
    source: str
    source_ref: str
    user_id: str
    mode: str
    prompt: str
    completion: str
    status: str
    quality_score: float
    safety_status: str
    created_at: int
    updated_at: int


def _safe_str(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _safe_mode(value: Any) -> str:
    mode = _safe_str(value, "chat").lower()
    return mode if mode in _SAFE_MODES else "chat"


def _safe_source(value: Any) -> str:
    source = _safe_str(value, "chat").lower()
    return source if source in _SAFE_SOURCES else "chat"


def _now() -> int:
    return int(time.time())


def _json_dumps(payload: dict[str, Any]) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return "{}"


def _normalize(text: Any) -> str:
    raw = _safe_str(text).lower().replace("đ", "d")
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = re.sub(r"[^a-z0-9\s]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _hash(text: Any) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


def _max_prompt_len() -> int:
    return max(200, int(getattr(settings, "AI_TRAINING_MAX_PROMPT_CHARS", 1800) or 1800))


def _max_completion_len() -> int:
    return max(200, int(getattr(settings, "AI_TRAINING_MAX_COMPLETION_CHARS", 5000) or 5000))


def _min_prompt_len() -> int:
    return max(2, int(getattr(settings, "AI_TRAINING_MIN_PROMPT_CHARS", 6) or 6))


def _enabled() -> bool:
    return bool(getattr(settings, "AI_TRAINING_CAPTURE_ENABLED", True))


_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(password|passwd|pwd|token|secret|api\s*key|private\s*key|authorization|bearer)\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b(password|passwd|pwd|token|secret|api\s*key|private\s*key|authorization|bearer)\b\s+(?:is|la|là)\s+\S+"),
    re.compile(r"(?i)\b(api\s*key|private\s*key|bearer)\b\s+\S+"),
    re.compile(r"(?i)\b(mật\s*khẩu|mat\s*khau|mk|otp|2fa)\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b(mật\s*khẩu|mat\s*khau|mk|otp|2fa)\b\s+(?:(?:của|cua)\s+(?:tôi|toi|em|anh|chị|chi|mình|minh)\s+)?(?:là|la)\s+\S+"),
    re.compile(r"(?i)\b(mk|otp|2fa)\b\s+\S+"),
    re.compile(r"(?i)\b(?:redis|postgres|postgresql|mysql|mongodb)://[^\s]+"),
    re.compile(r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)

_INTERNAL_HINTS = (
    "system prompt",
    "developer message",
    "instruction noi bo",
    "prompt noi bo",
    "redis key",
    "mt5:runner:",
    "mt5:execution:",
    "postgres",
    "postgresql",
    "pgbouncer",
    "database url",
    "backend api key",
    "source code",
    "ma nguon",
    "file nao",
    "nginx",
    "pm2",
    "docker",
    "server noi bo",
    "dia chi server",
    "linux backend",
    "control plane",
    "runner_id",
    "slot_id",
    "deployment_id",
    "command_id",
)

_DYNAMIC_HINTS = (
    "hom nay",
    "ngay mai",
    "hien tai",
    "bay gio",
    "moi nhat",
    "tin tuc",
    "so du",
    "balance",
    "equity",
    "bot cua toi",
    "tai khoan cua toi",
    "lenh cua toi",
    "dang chay",
)

_MARKET_SYMBOL_HINTS = (
    "gia vang",
    "xauusd",
    "btc",
    "bitcoin",
    "crypto",
    "eurusd",
    "usdjpy",
    "gbpusd",
)

_MARKET_DYNAMIC_HINTS = (
    "hom nay",
    "ngay mai",
    "hien tai",
    "bay gio",
    "moi nhat",
    "tin tuc",
    "tin moi",
    "gia bao nhieu",
    "dang bao nhieu",
    "bao nhieu",
    "len hay xuong",
    "du bao",
    "forecast",
)


def _looks_dynamic_runtime_content(normalized: str) -> bool:
    if any(hint in normalized for hint in _DYNAMIC_HINTS):
        return True
    return any(symbol in normalized for symbol in _MARKET_SYMBOL_HINTS) and any(
        hint in normalized for hint in _MARKET_DYNAMIC_HINTS
    )


def redact_training_text(text: Any) -> tuple[str, int]:
    value = _safe_str(text)
    redactions = 0
    for pattern in _SECRET_PATTERNS:
        value, count = pattern.subn(_REDACTED, value)
        redactions += count
    value = re.sub(r"\s+", " ", value).strip()
    return value, redactions


def training_skip_reason(
    *,
    prompt: Any,
    completion: Any,
    mode: Any = "chat",
    context: Optional[dict[str, Any]] = None,
    use_search: bool = False,
) -> Optional[str]:
    if not _enabled():
        return "training_capture_disabled"

    prompt_s = _safe_str(prompt)
    completion_s = _safe_str(completion)
    if len(prompt_s) < _min_prompt_len():
        return "prompt_too_short"
    if len(prompt_s) > _max_prompt_len():
        return "prompt_too_long"
    if not completion_s:
        return "completion_empty"
    if len(completion_s) > _max_completion_len():
        return "completion_too_long"
    if use_search or _safe_mode(mode) == "market":
        return "dynamic_or_external_search"

    normalized = _normalize(f"{prompt_s} {completion_s}")
    if any(hint in normalized for hint in _INTERNAL_HINTS):
        return "internal_system_content"
    if _looks_dynamic_runtime_content(normalized):
        return "dynamic_runtime_content"
    if isinstance(context, dict) and any(
        context.get(key)
        for key in (
            "account_id",
            "deployment_id",
            "runner_id",
            "slot_id",
            "login_reservation_id",
            "command_id",
            "broker_account_id",
            "mt5_login",
        )
    ):
        return "runtime_context"

    redacted_prompt, prompt_redactions = redact_training_text(prompt_s)
    redacted_completion, completion_redactions = redact_training_text(completion_s)
    if prompt_redactions or completion_redactions:
        return "sensitive_content"
    if redacted_prompt == _REDACTED or redacted_completion == _REDACTED:
        return "sensitive_content"
    return None


class AITrainingDataStore:
    def __init__(self, store: Any = None) -> None:
        self.store = store

    def _store(self) -> Any:
        return self.store or get_process_store()

    def capture_candidate_sync(
        self,
        *,
        user_id: Any,
        prompt: Any,
        completion: Any,
        mode: Any = "chat",
        source: Any = "chat",
        source_ref: Any = "",
        status: str = "pending",
        quality_score: float = 0.0,
        metadata: Optional[dict[str, Any]] = None,
        context: Optional[dict[str, Any]] = None,
        use_search: bool = False,
    ) -> Optional[str]:
        safe_status = _safe_str(status, "pending").lower()
        if safe_status not in _SAFE_STATUSES:
            safe_status = "pending"
        skip_reason = training_skip_reason(
            prompt=prompt,
            completion=completion,
            mode=mode,
            context=context,
            use_search=use_search,
        )
        if skip_reason:
            return skip_reason

        safe_prompt, prompt_redactions = redact_training_text(prompt)
        safe_completion, completion_redactions = redact_training_text(completion)
        redaction_count = prompt_redactions + completion_redactions
        if redaction_count:
            return "sensitive_content"

        prompt_hash = _hash(safe_prompt)
        completion_hash = _hash(safe_completion)
        example_key = hashlib.sha256(f"{_safe_mode(mode)}:{prompt_hash}:{completion_hash}".encode("utf-8")).hexdigest()
        now_ts = _now()
        safe_metadata = {
            "capture": "automatic_pending",
            "requires_review": True,
            **dict(metadata or {}),
        }

        def _do(_con: Any, cur: Any) -> None:
            cur.execute(
                """
                INSERT INTO ai_training_examples(
                    example_key, source, source_ref, user_id, scope, mode,
                    prompt, completion, prompt_hash, completion_hash,
                    status, quality_score, safety_status, skip_reason,
                    redaction_count, metadata_json, created_at, updated_at
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'safe',NULL,%s,%s::jsonb,%s,%s)
                ON CONFLICT(example_key) DO UPDATE SET
                    source = EXCLUDED.source,
                    source_ref = COALESCE(NULLIF(EXCLUDED.source_ref, ''), ai_training_examples.source_ref),
                    quality_score = GREATEST(ai_training_examples.quality_score, EXCLUDED.quality_score),
                    metadata_json = ai_training_examples.metadata_json || EXCLUDED.metadata_json,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    example_key,
                    _safe_source(source),
                    _safe_str(source_ref)[:180],
                    _safe_str(user_id)[:120] or None,
                    "platform",
                    _safe_mode(mode),
                    safe_prompt[: _max_prompt_len()],
                    safe_completion[: _max_completion_len()],
                    prompt_hash,
                    completion_hash,
                    safe_status,
                    max(0.0, min(float(quality_score or 0.0), 1.0)),
                    redaction_count,
                    _json_dumps(safe_metadata),
                    now_ts,
                    now_ts,
                ),
            )

        try:
            self._store()._with_retry_locked(_do)
            return None
        except Exception as exc:
            log.warning("AI training candidate capture skipped due to DB error: %s", str(exc)[:180])
            return "db_error"

    def load_exportable_examples_sync(
        self,
        *,
        mode: str = "",
        limit: int = 1000,
        min_quality: float = 0.0,
    ) -> list[TrainingExample]:
        safe_limit = max(1, min(int(limit or 1000), 10000))
        safe_mode = _safe_mode(mode) if mode else ""
        min_quality_f = max(0.0, min(float(min_quality or 0.0), 1.0))

        def _do(_con: Any, cur: Any) -> list[TrainingExample]:
            params: list[Any] = [min_quality_f]
            where = [
                "status = 'approved'",
                "safety_status = 'safe'",
                "quality_score >= %s",
            ]
            if safe_mode:
                where.append("mode = %s")
                params.append(safe_mode)
            params.append(safe_limit)
            cur.execute(
                f"""
                SELECT id, prompt, completion, mode, source, quality_score
                FROM ai_training_examples
                WHERE {' AND '.join(where)}
                ORDER BY quality_score DESC, updated_at DESC, id DESC
                LIMIT %s
                """,
                tuple(params),
            )
            rows = cur.fetchall() or []
            return [
                TrainingExample(
                    id=int(row.get("id") or 0),
                    prompt=_safe_str(row.get("prompt")),
                    completion=_safe_str(row.get("completion")),
                    mode=_safe_str(row.get("mode"), "chat") or "chat",
                    source=_safe_str(row.get("source"), "chat") or "chat",
                    quality_score=float(row.get("quality_score") or 0.0),
                )
                for row in rows
            ]

        return self._store()._with_retry_read(_do)

    def list_examples_sync(
        self,
        *,
        status: str = "pending",
        mode: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[TrainingExamplePreview]:
        safe_status = _safe_str(status, "pending").lower()
        if safe_status not in _SAFE_STATUSES:
            safe_status = "pending"
        safe_mode = _safe_mode(mode) if mode else ""
        safe_limit = max(1, min(int(limit or 50), 200))
        safe_offset = max(0, int(offset or 0))

        def _do(_con: Any, cur: Any) -> list[TrainingExamplePreview]:
            params: list[Any] = [safe_status]
            where = ["status = %s"]
            if safe_mode:
                where.append("mode = %s")
                params.append(safe_mode)
            params.extend([safe_limit, safe_offset])
            cur.execute(
                f"""
                SELECT
                    id, source, source_ref, user_id, mode, prompt, completion,
                    status, quality_score, safety_status, created_at, updated_at
                FROM ai_training_examples
                WHERE {' AND '.join(where)}
                ORDER BY updated_at DESC, id DESC
                LIMIT %s OFFSET %s
                """,
                tuple(params),
            )
            rows = cur.fetchall() or []
            return [
                TrainingExamplePreview(
                    id=int(row.get("id") or 0),
                    source=_safe_str(row.get("source")),
                    source_ref=_safe_str(row.get("source_ref")),
                    user_id=_safe_str(row.get("user_id")),
                    mode=_safe_str(row.get("mode"), "chat") or "chat",
                    prompt=_safe_str(row.get("prompt")),
                    completion=_safe_str(row.get("completion")),
                    status=_safe_str(row.get("status")),
                    quality_score=float(row.get("quality_score") or 0.0),
                    safety_status=_safe_str(row.get("safety_status")),
                    created_at=int(row.get("created_at") or 0),
                    updated_at=int(row.get("updated_at") or 0),
                )
                for row in rows
            ]

        return self._store()._with_retry_read(_do)

    def stats_sync(self) -> dict[str, Any]:
        def _do(_con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM ai_training_examples
                GROUP BY status
                ORDER BY status
                """
            )
            status_rows = cur.fetchall() or []
            cur.execute(
                """
                SELECT mode, COUNT(*) AS count
                FROM ai_training_examples
                GROUP BY mode
                ORDER BY mode
                """
            )
            mode_rows = cur.fetchall() or []
            cur.execute("SELECT COUNT(*) AS count FROM ai_training_exports")
            export_row = cur.fetchone() if hasattr(cur, "fetchone") else None
            cur.execute("SELECT COUNT(*) AS count FROM ai_model_versions")
            model_row = cur.fetchone() if hasattr(cur, "fetchone") else None
            cur.execute("SELECT COUNT(*) AS count FROM ai_model_eval_runs")
            eval_row = cur.fetchone() if hasattr(cur, "fetchone") else None
            return {
                "examples_by_status": {_safe_str(row.get("status")): int(row.get("count") or 0) for row in status_rows},
                "examples_by_mode": {_safe_str(row.get("mode")): int(row.get("count") or 0) for row in mode_rows},
                "export_count": int((export_row or {}).get("count") or 0),
                "model_version_count": int((model_row or {}).get("count") or 0),
                "eval_run_count": int((eval_row or {}).get("count") or 0),
            }

        return self._store()._with_retry_read(_do)

    def record_export_sync(
        self,
        *,
        export_key: str,
        output_path: str,
        checksum: str,
        example_ids: list[int],
        metadata: Optional[dict[str, Any]] = None,
        mark_exported: bool = False,
    ) -> None:
        now_ts = _now()
        safe_ids = [int(item) for item in example_ids if int(item) > 0]

        def _do(_con: Any, cur: Any) -> None:
            cur.execute(
                """
                INSERT INTO ai_training_exports(
                    export_key, format, output_path, checksum, example_count,
                    status, metadata_json, created_at
                )
                VALUES(%s,'jsonl',%s,%s,%s,'created',%s::jsonb,%s)
                ON CONFLICT(export_key) DO UPDATE SET
                    output_path = EXCLUDED.output_path,
                    checksum = EXCLUDED.checksum,
                    example_count = EXCLUDED.example_count,
                    status = EXCLUDED.status,
                    metadata_json = EXCLUDED.metadata_json
                """,
                (
                    export_key,
                    str(Path(output_path)),
                    checksum,
                    len(safe_ids),
                    _json_dumps(dict(metadata or {})),
                    now_ts,
                ),
            )
            if mark_exported and safe_ids:
                cur.execute(
                    """
                    UPDATE ai_training_examples
                    SET status = 'exported',
                        exported_at = %s,
                        updated_at = %s
                    WHERE id = ANY(%s)
                      AND status = 'approved'
                    """,
                    (now_ts, now_ts, safe_ids),
                )

        self._store()._with_retry_locked(_do)

    def review_examples_sync(
        self,
        *,
        example_ids: list[int],
        status: str,
        reviewer_id: str,
        quality_score: Optional[float] = None,
        note: str = "",
    ) -> int:
        safe_status = _safe_str(status).lower()
        if safe_status not in {"approved", "rejected"}:
            raise ValueError("review_status_must_be_approved_or_rejected")
        safe_ids = [int(item) for item in example_ids if int(item) > 0]
        if not safe_ids:
            return 0
        now_ts = _now()
        safe_reviewer = _safe_str(reviewer_id, "system")[:120] or "system"
        score = None if quality_score is None else max(0.0, min(float(quality_score), 1.0))
        metadata_patch = _json_dumps({"review_note": _safe_str(note)[:500]} if note else {})

        def _do(_con: Any, cur: Any) -> int:
            cur.execute(
                """
                UPDATE ai_training_examples
                SET status = %s,
                    reviewer_id = %s,
                    reviewed_at = %s,
                    quality_score = COALESCE(%s, quality_score),
                    metadata_json = metadata_json || %s::jsonb,
                    updated_at = %s
                WHERE id = ANY(%s)
                  AND status = 'pending'
                  AND safety_status = 'safe'
                """,
                (safe_status, safe_reviewer, now_ts, score, metadata_patch, now_ts, safe_ids),
            )
            return int(cur.rowcount or 0)

        return self._store()._with_retry_locked(_do)

    def register_model_version_sync(
        self,
        *,
        model_key: str,
        base_model: str,
        adapter_path: str = "",
        dataset_export_key: str = "",
        status: str = "candidate",
        metrics: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        safe_model_key = _safe_str(model_key)[:180]
        safe_base_model = _safe_str(base_model)[:180]
        if not safe_model_key or not safe_base_model:
            raise ValueError("model_key_and_base_model_required")
        safe_status = _safe_str(status, "candidate").lower()
        if safe_status not in {"candidate", "staging", "active", "retired", "failed"}:
            safe_status = "candidate"
        now_ts = _now()

        def _do(_con: Any, cur: Any) -> None:
            cur.execute(
                """
                INSERT INTO ai_model_versions(
                    model_key, base_model, adapter_path, dataset_export_key, status,
                    metrics_json, metadata_json, created_at, activated_at, retired_at
                )
                VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,
                    CASE WHEN %s = 'active' THEN %s ELSE NULL END,
                    CASE WHEN %s = 'retired' THEN %s ELSE NULL END)
                ON CONFLICT(model_key) DO UPDATE SET
                    base_model = EXCLUDED.base_model,
                    adapter_path = EXCLUDED.adapter_path,
                    dataset_export_key = EXCLUDED.dataset_export_key,
                    status = EXCLUDED.status,
                    metrics_json = EXCLUDED.metrics_json,
                    metadata_json = EXCLUDED.metadata_json,
                    activated_at = CASE WHEN EXCLUDED.status = 'active'
                        THEN COALESCE(ai_model_versions.activated_at, EXCLUDED.activated_at)
                        ELSE ai_model_versions.activated_at END,
                    retired_at = CASE WHEN EXCLUDED.status = 'retired'
                        THEN COALESCE(ai_model_versions.retired_at, EXCLUDED.retired_at)
                        ELSE ai_model_versions.retired_at END
                """,
                (
                    safe_model_key,
                    safe_base_model,
                    _safe_str(adapter_path)[:1000] or None,
                    _safe_str(dataset_export_key)[:180] or None,
                    safe_status,
                    _json_dumps(dict(metrics or {})),
                    _json_dumps(dict(metadata or {})),
                    now_ts,
                    safe_status,
                    now_ts,
                    safe_status,
                    now_ts,
                ),
            )

        self._store()._with_retry_locked(_do)

    def record_eval_run_sync(
        self,
        *,
        run_key: str,
        model_key: str,
        dataset_export_key: str = "",
        eval_type: str = "dataset_static",
        status: str = "completed",
        example_count: int = 0,
        score: float = 0.0,
        pass_threshold: float = 0.8,
        metrics: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        safe_run_key = _safe_str(run_key)[:180]
        safe_model_key = _safe_str(model_key)[:180] or "dataset"
        if not safe_run_key:
            raise ValueError("run_key_required")
        safe_status = _safe_str(status, "completed").lower()
        if safe_status not in {"created", "running", "completed", "failed"}:
            safe_status = "completed"
        safe_score = max(0.0, min(float(score or 0.0), 1.0))
        safe_threshold = max(0.0, min(float(pass_threshold or 0.0), 1.0))
        now_ts = _now()

        def _do(_con: Any, cur: Any) -> None:
            cur.execute(
                """
                INSERT INTO ai_model_eval_runs(
                    run_key, model_key, dataset_export_key, eval_type, status,
                    example_count, score, pass_threshold, passed,
                    metrics_json, metadata_json, created_at, completed_at
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,
                    CASE WHEN %s IN ('completed', 'failed') THEN %s ELSE NULL END)
                ON CONFLICT(run_key) DO UPDATE SET
                    model_key = EXCLUDED.model_key,
                    dataset_export_key = EXCLUDED.dataset_export_key,
                    eval_type = EXCLUDED.eval_type,
                    status = EXCLUDED.status,
                    example_count = EXCLUDED.example_count,
                    score = EXCLUDED.score,
                    pass_threshold = EXCLUDED.pass_threshold,
                    passed = EXCLUDED.passed,
                    metrics_json = EXCLUDED.metrics_json,
                    metadata_json = EXCLUDED.metadata_json,
                    completed_at = EXCLUDED.completed_at
                """,
                (
                    safe_run_key,
                    safe_model_key,
                    _safe_str(dataset_export_key)[:180] or None,
                    _safe_str(eval_type, "dataset_static")[:60],
                    safe_status,
                    max(0, int(example_count or 0)),
                    safe_score,
                    safe_threshold,
                    safe_score >= safe_threshold,
                    _json_dumps(dict(metrics or {})),
                    _json_dumps(dict(metadata or {})),
                    now_ts,
                    safe_status,
                    now_ts,
                ),
            )

        self._store()._with_retry_locked(_do)


def capture_training_candidate(
    *,
    user_id: Any,
    prompt: Any,
    completion: Any,
    mode: Any = "chat",
    source: Any = "chat",
    source_ref: Any = "",
    metadata: Optional[dict[str, Any]] = None,
    context: Optional[dict[str, Any]] = None,
    use_search: bool = False,
) -> Optional[str]:
    return AITrainingDataStore().capture_candidate_sync(
        user_id=user_id,
        prompt=prompt,
        completion=completion,
        mode=mode,
        source=source,
        source_ref=source_ref,
        metadata=metadata,
        context=context,
        use_search=use_search,
    )
