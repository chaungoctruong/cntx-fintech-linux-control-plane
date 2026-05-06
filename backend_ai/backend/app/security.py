from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from cryptography.fernet import Fernet, InvalidToken


def _derive_fernet_key(secret: str) -> bytes:
    # deterministic key from secret string (MVP-friendly)
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _mk_fernet(secret: str) -> Fernet:
    return Fernet(_derive_fernet_key(secret))


@dataclass
class CryptoBox:
    """
    Startup-grade MVP:
    - encrypt: always v1 envelope
    - decrypt: supports key rotation (current + old secrets)
    """
    secret: str
    old_secrets: Optional[list[str]] = None
    version: str = "v1"

    @property
    def _fernets(self) -> list[Fernet]:
        ferns: list[Fernet] = []
        if self.secret:
            ferns.append(_mk_fernet(self.secret))
        for s in (self.old_secrets or []):
            s = (s or "").strip()
            if s:
                ferns.append(_mk_fernet(s))
        return ferns

    def encrypt_json(self, obj: Dict[str, Any]) -> str:
        raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        token = _mk_fernet(self.secret).encrypt(raw).decode("utf-8")
        return f"{self.version}:{token}"

    def decrypt_json(self, token: str, *, ttl_sec: Optional[int] = None) -> Dict[str, Any]:
        """
        ttl_sec: optional TTL validation by Fernet (uses embedded timestamp).
        """
        if not token:
            raise ValueError("empty token")

        token = token.strip()
        # unwrap envelope
        if token.startswith("v1:"):
            token_body = token[3:]
        else:
            # backward compatible: old data had no version prefix
            token_body = token

        last_err: Optional[Exception] = None
        for f in self._fernets:
            try:
                raw = f.decrypt(token_body.encode("utf-8"), ttl=ttl_sec) if ttl_sec else f.decrypt(token_body.encode("utf-8"))
                return json.loads(raw.decode("utf-8"))
            except InvalidToken as e:
                last_err = e
                continue
            except Exception as e:
                last_err = e
                continue

        raise ValueError("decrypt failed") from last_err