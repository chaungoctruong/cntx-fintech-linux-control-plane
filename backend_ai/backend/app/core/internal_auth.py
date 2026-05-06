from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException, status

from app.settings import settings


async def require_backend_api_key(
    x_backend_api_key: Optional[str] = Header(default=None, alias="X-Backend-Api-Key"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> dict[str, str]:
    expected = str(getattr(settings, "BACKEND_API_KEY", "") or "").strip()
    if not expected:
        # Local/dev compatibility: do not block when API key has not been configured yet.
        return {"mode": "unprotected"}

    provided = str(x_backend_api_key or "").strip()
    if not provided and authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()

    if provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_backend_api_key",
        )
    return {"mode": "protected"}
