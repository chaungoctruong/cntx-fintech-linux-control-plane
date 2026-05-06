from __future__ import annotations

from typing import Optional


class AIServiceError(RuntimeError):
    """Base class for AI route/provider failures that should map to user-facing status codes."""


class AIOverloadedError(AIServiceError):
    def __init__(
        self,
        message: str = "ai_overloaded",
        *,
        retry_after_sec: int = 15,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_sec = max(1, int(retry_after_sec))
        self.detail = str(detail or message)


class AIProviderUnavailableError(AIServiceError):
    def __init__(
        self,
        message: str = "ai_provider_unavailable",
        *,
        retry_after_sec: int = 30,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_sec = max(1, int(retry_after_sec))
        self.detail = str(detail or message)
