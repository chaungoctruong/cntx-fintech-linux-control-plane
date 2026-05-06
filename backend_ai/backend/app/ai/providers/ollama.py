from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from app.ai.errors import AIOverloadedError, AIProviderUnavailableError
from app.ai.prompts import CNTX_LABS_ASSISTANT_SYSTEM_PROMPT
from app.ai.runtime_config import (
    TINY_MODEL_WARNING,
    get_ollama_base_url,
    get_ollama_model_with_source,
    is_tiny_local_model,
    warn_if_tiny_local_model,
)
from app.settings import settings

logger = logging.getLogger("CNTx labs_Ollama")

OLLAMA_COMPACT_SYSTEM_PROMPT = CNTX_LABS_ASSISTANT_SYSTEM_PROMPT


class OllamaProvider:
    def __init__(self) -> None:
        self.model_name, self.model_source = get_ollama_model_with_source()
        self.base_url = get_ollama_base_url()
        self.keep_alive = str(
            os.getenv("OLLAMA_KEEP_ALIVE")
            or getattr(settings, "OLLAMA_KEEP_ALIVE", "5m")
        ).strip()
        self.max_concurrent = max(1, int(getattr(settings, "AI_LOCAL_MAX_CONCURRENT", 1) or 1))
        self.max_queued = max(0, int(getattr(settings, "AI_LOCAL_MAX_QUEUED", 4) or 4))
        self.queue_wait_sec = max(0.1, float(getattr(settings, "AI_LOCAL_QUEUE_WAIT_SEC", 3.0) or 3.0))
        self.retry_after_sec = max(1, int(getattr(settings, "AI_CHAT_RETRY_AFTER_SEC", 15) or 15))
        self._client: Optional[httpx.AsyncClient] = None
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._waiting = 0
        self._inflight = 0
        self._last_ok_at = 0.0
        self._last_error = ""
        self._model_installed_cache: Optional[bool] = None
        self._model_installed_checked_at = 0.0
        warn_if_tiny_local_model(self.model_name)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = float(getattr(settings, "OLLAMA_TIMEOUT_SEC", 45.0) or 45.0)
            self._client = httpx.AsyncClient(
                timeout=timeout,
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    def is_configured(self) -> bool:
        return bool(self.base_url and self.model_name)

    def _endpoint_host_port(self) -> tuple[str, int]:
        parsed = urlparse(self.base_url if "://" in self.base_url else f"http://{self.base_url}")
        host = parsed.hostname or "127.0.0.1"
        port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
        return host, port

    def sync_available(self) -> bool:
        if not self.is_configured():
            return False
        try:
            host, port = self._endpoint_host_port()
            with socket.create_connection((host, port), timeout=0.35):
                return True
        except Exception:
            return False

    def sync_model_installed(self) -> bool:
        if not self.sync_available():
            return False
        now = time.time()
        if self._model_installed_cache is not None and now - self._model_installed_checked_at < 15:
            return bool(self._model_installed_cache)
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=0.8)
            response.raise_for_status()
            data = response.json()
            model_names = {
                str(item.get("name") or item.get("model") or "").strip()
                for item in (data.get("models") or [])
                if isinstance(item, dict)
            }
            installed = self.model_name in model_names
            self._model_installed_cache = installed
            self._model_installed_checked_at = now
            return installed
        except Exception as exc:
            self._last_error = f"ollama_model_check_failed:{str(exc)[:120]}"
            self._model_installed_cache = False
            self._model_installed_checked_at = now
            return False

    def runtime_status(self) -> dict[str, Any]:
        endpoint_available = self.sync_available()
        model_installed = self.sync_model_installed() if endpoint_available else False
        too_small = is_tiny_local_model(self.model_name)
        return {
            "provider": "ollama",
            "configured": self.is_configured(),
            "available": bool(endpoint_available and model_installed),
            "model": self.model_name,
            "model_source": self.model_source,
            "model_installed": bool(model_installed),
            "too_small_for_production": bool(too_small),
            "warning": TINY_MODEL_WARNING if too_small else "",
            "base_url": self.base_url,
            "max_concurrent": self.max_concurrent,
            "queued": int(self._waiting),
            "max_queued": self.max_queued,
            "inflight": int(self._inflight),
            "queue_wait_sec": self.queue_wait_sec,
            "retry_after_sec": self.retry_after_sec,
            "last_ok_at": int(self._last_ok_at) if self._last_ok_at > 0 else 0,
            "last_error": self._last_error[:180],
        }

    async def generate_response(
        self,
        user_query: str,
        *,
        system_prompt: Optional[str] = None,
        use_google_search: bool = False,
        temperature: float = 0.55,
        max_output_tokens: int = 450,
        top_p: float = 0.9,
        response_mime_type: Optional[str] = None,
        response_json_schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not self.base_url or not self.model_name:
            raise AIProviderUnavailableError("ollama_provider_not_configured", retry_after_sec=self.retry_after_sec)

        if not self.sync_available():
            raise AIProviderUnavailableError("ollama_unreachable", retry_after_sec=self.retry_after_sec)

        if not self.sync_model_installed():
            raise AIProviderUnavailableError(
                f"ollama_model_not_installed:{self.model_name}",
                retry_after_sec=self.retry_after_sec,
            )

        if use_google_search:
            logger.info("Ollama provider ignores google_search flag for local inference")

        if self._waiting >= self.max_queued:
            raise AIOverloadedError(
                "ai_overloaded",
                retry_after_sec=self.retry_after_sec,
                detail=f"queue_full waiting={self._waiting} max_queued={self.max_queued}",
            )

        final_system_prompt = (system_prompt or OLLAMA_COMPACT_SYSTEM_PROMPT).strip()
        messages: list[dict[str, str]] = []
        if final_system_prompt:
            messages.append({"role": "system", "content": final_system_prompt})
        messages.append({"role": "user", "content": str(user_query or "").strip()})

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
            "keep_alive": self.keep_alive or "5m",
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "num_predict": int(max_output_tokens or 450),
            },
        }

        if response_json_schema:
            payload["format"] = response_json_schema
        elif response_mime_type and "json" in response_mime_type.lower():
            payload["format"] = "json"

        client = await self._get_client()
        retries = 2
        delays = [1.0, 2.0]
        self._waiting += 1
        acquired = False

        try:
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=self.queue_wait_sec)
                acquired = True
            except asyncio.TimeoutError as exc:
                self._last_error = "queue_wait_timeout"
                raise AIOverloadedError(
                    "ai_busy_retry",
                    retry_after_sec=self.retry_after_sec,
                    detail=f"queue_wait_timeout>{self.queue_wait_sec}s",
                ) from exc

            self._waiting -= 1
            self._inflight += 1

            for attempt in range(retries + 1):
                try:
                    response = await client.post(f"{self.base_url}/api/chat", json=payload)
                    if response.status_code == 200:
                        result = response.json()
                        content = str(((result.get("message") or {}).get("content") or "")).strip()
                        if content:
                            self._last_ok_at = time.time()
                            self._last_error = ""
                            return content
                        raise RuntimeError("empty_response_from_ollama")

                    body_preview = response.text[:400]
                    logger.error(
                        "OLLAMA API ERROR (attempt %s): %s - %s",
                        attempt + 1,
                        response.status_code,
                        body_preview,
                    )
                    self._last_error = f"ollama_http_{response.status_code}"
                    if response.status_code in (429, 503):
                        raise AIOverloadedError(
                            "ai_overloaded",
                            retry_after_sec=self.retry_after_sec,
                            detail=self._last_error,
                        )
                    if response.status_code not in (408, 500, 502, 504):
                        raise RuntimeError(f"ollama_http_{response.status_code}")
                except AIOverloadedError:
                    raise
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    self._last_error = str(exc)[:180]
                    logger.warning("OLLAMA transport error (attempt %s): %s", attempt + 1, exc)
                except Exception:
                    if attempt >= retries:
                        raise

                if attempt < retries:
                    await asyncio.sleep(delays[attempt])

            raise RuntimeError("ollama_request_failed")
        finally:
            if not acquired:
                self._waiting = max(0, self._waiting - 1)
            else:
                self._inflight = max(0, self._inflight - 1)
                try:
                    self._semaphore.release()
                except ValueError:
                    pass


ollama_engine = OllamaProvider()
