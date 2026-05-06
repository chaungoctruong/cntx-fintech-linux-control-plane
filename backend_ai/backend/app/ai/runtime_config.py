from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from app.settings import settings

log = logging.getLogger("ai.runtime_config")

SUPPORTED_AI_PROVIDERS = {"ollama", "gemini", "auto"}
DEFAULT_AI_PROVIDER = "ollama"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:14b"
FALLBACK_OLLAMA_MODEL = "qwen3:8b"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"
TINY_MODEL_WARNING = "WARNING: current local model is too small for trading assistant production."


@dataclass(frozen=True)
class AIRuntimeConfig:
    provider: str
    ollama_base_url: str
    ollama_model: str
    ollama_model_source: str
    gemini_model: str
    fallback_enabled: bool


def _clean(value: object) -> str:
    return str(value or "").strip()


def normalize_ai_provider(value: object) -> str:
    provider = _clean(value).lower()
    if provider not in SUPPORTED_AI_PROVIDERS:
        return DEFAULT_AI_PROVIDER
    return provider


def get_ai_provider() -> str:
    raw = (
        os.getenv("AI_PROVIDER")
        or os.getenv("AI_CHAT_PROVIDER")
        or _clean(getattr(settings, "AI_PROVIDER", ""))
        or _clean(getattr(settings, "AI_CHAT_PROVIDER", ""))
        or DEFAULT_AI_PROVIDER
    )
    return normalize_ai_provider(raw)


def get_ollama_base_url() -> str:
    return (
        os.getenv("OLLAMA_BASE_URL")
        or _clean(getattr(settings, "OLLAMA_BASE_URL", ""))
        or DEFAULT_OLLAMA_BASE_URL
    ).rstrip("/")


def get_ollama_model_with_source() -> tuple[str, str]:
    fields_set = set(getattr(settings, "model_fields_set", set()) or set())
    candidates = (
        ("OLLAMA_MODEL", os.getenv("OLLAMA_MODEL")),
        ("settings.OLLAMA_MODEL", _clean(getattr(settings, "OLLAMA_MODEL", "")) if "OLLAMA_MODEL" in fields_set else ""),
        ("AI_MODEL", os.getenv("AI_MODEL")),
        ("settings.AI_MODEL", _clean(getattr(settings, "AI_MODEL", "")) if "AI_MODEL" in fields_set else ""),
    )
    for source, value in candidates:
        model = _clean(value)
        if model:
            return model, source
    return FALLBACK_OLLAMA_MODEL, "fallback"


def get_ollama_model() -> str:
    model, _ = get_ollama_model_with_source()
    return model


def get_gemini_model() -> str:
    return (
        os.getenv("GEMINI_MODEL")
        or _clean(getattr(settings, "GEMINI_MODEL", ""))
        or DEFAULT_GEMINI_MODEL
    )


def local_model_size_b(model: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*b", _clean(model).lower())
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def is_tiny_local_model(model: str) -> bool:
    size = local_model_size_b(model)
    return bool(size is not None and size <= 1.5)


def warn_if_tiny_local_model(model: str) -> None:
    if is_tiny_local_model(model):
        log.warning(TINY_MODEL_WARNING)


def gemini_fallback_enabled() -> bool:
    gemini_key = _clean(os.getenv("GEMINI_API_KEY") or getattr(settings, "GEMINI_API_KEY", ""))
    if not gemini_key:
        return False
    return bool(
        getattr(settings, "AI_CHAT_OLLAMA_USE_GEMINI_FOR_SEARCH", False)
        or getattr(settings, "AI_CHAT_OLLAMA_USE_GEMINI_FOR_COMPLEX", False)
    )


def get_ai_runtime_config() -> AIRuntimeConfig:
    ollama_model, ollama_source = get_ollama_model_with_source()
    return AIRuntimeConfig(
        provider=get_ai_provider(),
        ollama_base_url=get_ollama_base_url(),
        ollama_model=ollama_model,
        ollama_model_source=ollama_source,
        gemini_model=get_gemini_model(),
        fallback_enabled=gemini_fallback_enabled(),
    )
