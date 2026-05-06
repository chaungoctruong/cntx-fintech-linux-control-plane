from __future__ import annotations

import logging
import math
import threading
from typing import Any

from app.settings import settings

log = logging.getLogger("ai_embedding_provider")

_MODEL_LOCK = threading.Lock()
_MODEL: Any = None
_MODEL_KEY = ""
_LAST_ERROR = ""
_MISSING_DEP_LOGGED = False


def _clean(value: Any) -> str:
    return str(value or "").strip()


def embedding_enabled() -> bool:
    return bool(getattr(settings, "AI_PLATFORM_KNOWLEDGE_VECTOR_ENABLED", False))


def embedding_provider_name() -> str:
    return _clean(getattr(settings, "AI_PLATFORM_KNOWLEDGE_EMBEDDING_PROVIDER", "sentence_transformers")).lower()


def embedding_model_name() -> str:
    return _clean(getattr(settings, "AI_PLATFORM_KNOWLEDGE_EMBEDDING_MODEL", "BAAI/bge-m3")) or "BAAI/bge-m3"


def _embedding_device() -> str:
    return _clean(getattr(settings, "AI_PLATFORM_KNOWLEDGE_EMBEDDING_DEVICE", "cpu")) or "cpu"


def _normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(float(item) * float(item) for item in values))
    if norm <= 0:
        return values
    return [round(float(item) / norm, 8) for item in values]


def _coerce_vector(value: Any) -> list[float]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if value and isinstance(value[0], list):
        value = value[0]
    out: list[float] = []
    for item in value or []:
        number = float(item)
        if not math.isfinite(number):
            return []
        out.append(number)
    return out


def _load_sentence_transformer() -> Any:
    global _MODEL, _MODEL_KEY, _LAST_ERROR, _MISSING_DEP_LOGGED

    model_name = embedding_model_name()
    device = _embedding_device()
    key = f"sentence_transformers:{model_name}:{device}"
    if _MODEL is not None and _MODEL_KEY == key:
        return _MODEL

    with _MODEL_LOCK:
        if _MODEL is not None and _MODEL_KEY == key:
            return _MODEL
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:
            _LAST_ERROR = f"sentence_transformers_unavailable:{type(exc).__name__}"
            if not _MISSING_DEP_LOGGED:
                _MISSING_DEP_LOGGED = True
                log.warning(
                    "AI vector embeddings disabled: install optional requirements-ai-vector.txt to use sentence-transformers"
                )
            return None

        try:
            _MODEL = SentenceTransformer(model_name, device=device)
            _MODEL_KEY = key
            _LAST_ERROR = ""
            return _MODEL
        except Exception as exc:
            _LAST_ERROR = f"embedding_model_load_failed:{type(exc).__name__}"
            log.warning("AI embedding model load failed model=%s device=%s err=%s", model_name, device, str(exc)[:180])
            return None


def embed_text_sync(text: str) -> list[float] | None:
    if not embedding_enabled():
        return None
    provider = embedding_provider_name()
    if provider not in {"sentence_transformers", "sentence-transformer"}:
        global _LAST_ERROR
        _LAST_ERROR = f"unsupported_embedding_provider:{provider or 'empty'}"
        return None

    clean_text = _clean(text)
    if not clean_text:
        return None
    model = _load_sentence_transformer()
    if model is None:
        return None
    try:
        encoded = model.encode(clean_text, normalize_embeddings=True)
        values = _coerce_vector(encoded)
        return _normalize(values) if values else None
    except Exception as exc:
        _LAST_ERROR = f"embedding_encode_failed:{type(exc).__name__}"
        log.warning("AI embedding encode failed: %s", str(exc)[:180])
        return None


def embedding_runtime_status() -> dict[str, Any]:
    provider = embedding_provider_name()
    model = embedding_model_name()
    return {
        "enabled": embedding_enabled(),
        "provider": provider,
        "model": model,
        "device": _embedding_device(),
        "pgvector_dim": int(getattr(settings, "AI_PLATFORM_KNOWLEDGE_PGVECTOR_DIM", 1024) or 1024),
        "loaded": bool(_MODEL is not None),
        "last_error": _LAST_ERROR[:180],
    }
