from __future__ import annotations

from typing import Any

from app.ai.runtime_config import get_ai_runtime_config
from app.settings import settings

try:
    from app.ai.providers.gemini import gemini_engine
except Exception:  # pragma: no cover
    gemini_engine = None

try:
    from app.ai.providers.ollama import ollama_engine
except Exception:  # pragma: no cover
    ollama_engine = None

try:
    from app.ai.deferred_queue import deferred_ai_queue_status
except Exception:  # pragma: no cover
    def deferred_ai_queue_status() -> dict[str, Any]:
        return {"enabled": False, "worker_alive": False, "queued": 0, "max_queued": 0}

try:
    from app.ai.continuous_learning import ai_continuous_learning_status
except Exception:  # pragma: no cover
    def ai_continuous_learning_status() -> dict[str, Any]:
        return {"enabled": False, "worker_alive": False}

try:
    from app.ai.embedding_provider import embedding_runtime_status
except Exception:  # pragma: no cover
    def embedding_runtime_status() -> dict[str, Any]:
        return {"enabled": False, "loaded": False}


def _requested_provider() -> str:
    return get_ai_runtime_config().provider


def get_ai_runtime_status_sync() -> dict[str, Any]:
    cfg = get_ai_runtime_config()
    provider = cfg.provider
    status: dict[str, Any] = {
        "provider": provider,
        "available": False,
        "configured": False,
        "model": "",
        "ollama_base_url": cfg.ollama_base_url,
        "gemini_model": cfg.gemini_model,
        "fallback_enabled": bool(cfg.fallback_enabled),
        "details": {},
    }

    gemini_key = str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()

    if provider == "ollama":
        if ollama_engine is None:
            status["details"] = {
                "reason": "ollama_engine_missing",
                "continuous_learning": ai_continuous_learning_status(),
                "knowledge_embeddings": embedding_runtime_status(),
            }
            return status
        details = ollama_engine.runtime_status()
        status["configured"] = bool(details.get("configured"))
        status["available"] = bool(details.get("available"))
        status["model"] = str(details.get("model") or "")
        status["ollama_base_url"] = str(details.get("base_url") or cfg.ollama_base_url)
        details["deferred_queue"] = deferred_ai_queue_status()
        details["continuous_learning"] = ai_continuous_learning_status()
        details["knowledge_embeddings"] = embedding_runtime_status()
        details["gemini_model"] = cfg.gemini_model
        details["fallback_enabled"] = bool(cfg.fallback_enabled)
        status["details"] = details
        return status

    if provider == "auto":
        if ollama_engine is not None:
            details = ollama_engine.runtime_status()
            if details.get("configured"):
                status["provider"] = "ollama"
                status["configured"] = bool(details.get("configured"))
                status["available"] = bool(details.get("available"))
                status["model"] = str(details.get("model") or "")
                status["ollama_base_url"] = str(details.get("base_url") or cfg.ollama_base_url)
                details["deferred_queue"] = deferred_ai_queue_status()
                details["continuous_learning"] = ai_continuous_learning_status()
                details["knowledge_embeddings"] = embedding_runtime_status()
                details["gemini_model"] = cfg.gemini_model
                details["fallback_enabled"] = bool(cfg.fallback_enabled)
                status["details"] = details
                return status
        status["provider"] = "gemini"

    status["configured"] = bool(gemini_key)
    status["available"] = bool(gemini_key and gemini_engine is not None)
    status["model"] = cfg.gemini_model
    status["details"] = {
        "configured": bool(gemini_key),
        "engine_loaded": bool(gemini_engine is not None),
        "ollama_base_url": cfg.ollama_base_url,
        "gemini_model": cfg.gemini_model,
        "fallback_enabled": bool(cfg.fallback_enabled),
        "continuous_learning": ai_continuous_learning_status(),
        "knowledge_embeddings": embedding_runtime_status(),
    }
    return status


def is_ai_available_sync() -> bool:
    return bool(get_ai_runtime_status_sync().get("available"))
