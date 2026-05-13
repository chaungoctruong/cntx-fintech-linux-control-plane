import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt


class TokenService:
    def __init__(self, secret: str, issuer: str = "token-bot", algorithm: str = "HS256"):
        if not secret:
            raise ValueError("jwt_secret không được rỗng")
        self._secret = secret
        self._issuer = issuer
        self._alg = algorithm

    def issue(
        self,
        partner_id: str,
        bot_ids: list[str],
        ttl_seconds: int,
        *,
        account_id: int | None = None,
        end_user_label: str | None = None,
    ) -> tuple[str, str, datetime]:
        jti = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        exp = now + timedelta(seconds=ttl_seconds)
        payload: dict[str, Any] = {
            "iss": self._issuer,
            "sub": partner_id,
            "jti": jti,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
            "scope": {"bot_ids": list(bot_ids)},
        }
        if account_id is not None:
            payload["account_id"] = int(account_id)
        if end_user_label:
            payload["end_user_label"] = end_user_label
        token = jwt.encode(payload, self._secret, algorithm=self._alg)
        return token, jti, exp

    def verify(self, token: str) -> dict[str, Any]:
        return jwt.decode(
            token,
            self._secret,
            algorithms=[self._alg],
            issuer=self._issuer,
            options={"require": ["exp", "iat", "iss", "sub", "jti"]},
        )
