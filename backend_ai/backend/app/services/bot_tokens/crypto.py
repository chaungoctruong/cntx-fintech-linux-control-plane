from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Iterable

from app.settings import settings


_HASH_PREFIX = "bt2_hmac_sha256:"


class BotTokenCryptoError(ValueError):
    pass


def _normalize_raw_token(raw_token: str) -> str:
    token = str(raw_token or "").strip()
    if not token:
        raise BotTokenCryptoError("bot_token_required")
    return token


def _active_secret_keys() -> list[str]:
    keys = [str(getattr(settings, "APP_SECRET_KEY", "") or "").strip()]
    try:
        keys.extend(settings.secret_old_keys())
    except Exception:
        pass
    return [key for key in keys if key]


def hash_token(raw_token: str, *, secret_key: str | None = None) -> str:
    token = _normalize_raw_token(raw_token)
    key = str(secret_key or getattr(settings, "APP_SECRET_KEY", "") or "").strip()
    if len(key) < 32:
        raise BotTokenCryptoError("bot_token_hash_secret_not_configured")
    digest = hmac.new(key.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{_HASH_PREFIX}{digest}"


def legacy_sha256_hash(raw_token: str) -> str:
    token = _normalize_raw_token(raw_token)
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_token_candidates(raw_token: str) -> list[str]:
    token = _normalize_raw_token(raw_token)
    candidates: list[str] = []
    for key in _active_secret_keys():
        try:
            candidates.append(hash_token(token, secret_key=key))
        except BotTokenCryptoError:
            continue
    if bool(getattr(settings, "BOT_TOKEN_ACCEPT_LEGACY_SHA256", True)):
        candidates.append(legacy_sha256_hash(token))
    seen: set[str] = set()
    out: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    if not out:
        raise BotTokenCryptoError("bot_token_hash_secret_not_configured")
    return out


def constant_time_hash_matches(raw_token: str, candidate_hashes: Iterable[str]) -> bool:
    calculated = hash_token_candidates(raw_token)
    for expected in candidate_hashes:
        expected_s = str(expected or "").strip()
        if any(hmac.compare_digest(expected_s, actual) for actual in calculated):
            return True
    return False


def generate_raw_token(*, bot_code: str, duration_days: int) -> str:
    bot = "".join(ch for ch in str(bot_code or "").strip().lower() if ch.isalnum() or ch in {"_", "-"})
    if not bot:
        bot = "bot"
    days = max(1, int(duration_days or 0))
    return f"cntx_{bot}_{days}d_{secrets.token_urlsafe(24)}"
