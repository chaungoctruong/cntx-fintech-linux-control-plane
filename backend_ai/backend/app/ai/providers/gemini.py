import os
import httpx
import asyncio
import logging
import sys
from typing import Optional, Dict, Any

# [PATH FIX]
CURRENT_FILE_PATH = os.path.abspath(__file__)
BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(CURRENT_FILE_PATH)
        )
    )
)

if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

try:
    from app.settings import settings
    from app.ai.errors import AIOverloadedError, AIProviderUnavailableError
    from app.ai.prompts import SYSTEM_REASSURANCE_PROMPT
    from app.ai.runtime_config import get_gemini_model
except ImportError as e:
    print(f"CRITICAL: Không thể import settings hoặc prompts. Path hiện tại: {sys.path}")
    raise e

logger = logging.getLogger("CNTx labs_Gemini")


class GeminiProvider:
    def __init__(self):
        # Rẻ + nhanh cho workload lớn
        self.model_name = get_gemini_model()

        self.base_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_name}:generateContent"
        )

        self._client: Optional[httpx.AsyncClient] = None

    @property
    def api_key(self) -> str:
        try:
            key = os.getenv("GEMINI_API_KEY") or getattr(settings, "GEMINI_API_KEY", "")
            return str(key).strip()
        except Exception as e:
            logger.error(f"Lỗi khi truy xuất API Key: {e}")
            return ""

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = float(getattr(settings, "GEMINI_TIMEOUT_SEC", 45.0) or 45.0)
            self._client = httpx.AsyncClient(
                timeout=timeout,
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
                headers={"Content-Type": "application/json"},
            )
        return self._client

    def _extract_text(self, result: Dict[str, Any]) -> str:
        try:
            candidates = result.get("candidates") or []
            if not candidates:
                return ""

            content = candidates[0].get("content") or {}
            parts = content.get("parts") or []
            texts = []

            for part in parts:
                txt = str(part.get("text") or "").strip()
                if txt:
                    texts.append(txt)

            return "\n".join(texts).strip()
        except Exception:
            return ""

    def _extract_grounding_links(self, result: Dict[str, Any], max_links: int = 2) -> list[tuple[str, str]]:
        links: list[tuple[str, str]] = []
        seen: set[str] = set()

        try:
            candidates = result.get("candidates") or []
            if not candidates:
                return links

            grounding = (candidates[0].get("groundingMetadata") or {})
            chunks = grounding.get("groundingChunks") or []

            for chunk in chunks:
                web = chunk.get("web") or {}
                uri = str(web.get("uri") or "").strip()
                title = str(web.get("title") or "").strip()

                if not uri or uri in seen:
                    continue

                seen.add(uri)
                links.append((title or "Nguồn", uri))

                if len(links) >= max_links:
                    break
        except Exception:
            return links

        return links

    def _append_grounding_links(
        self,
        text: str,
        result: Dict[str, Any],
        *,
        response_mime_type: Optional[str] = None,
        max_links: int = 2,
    ) -> str:
        # Nếu đang ép JSON thì không được chèn link vào, kẻo vỡ JSON
        if response_mime_type and "json" in response_mime_type.lower():
            return text

        links = self._extract_grounding_links(result, max_links=max_links)
        if not links:
            return text

        # Tránh append trùng nếu text đã có URL
        if "http://" in text or "https://" in text:
            return text

        link_lines = []
        for title, uri in links:
            link_lines.append(f"- {title}: {uri}")

        suffix = "\n\n🔗 Xem nhanh:\n" + "\n".join(link_lines)
        return (text or "").rstrip() + suffix

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

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
        """
        Production-safe generate:
        - system_prompt tùy task
        - use_google_search chỉ bật khi thật sự cần tin mới
        - max_output_tokens gọn để tiết kiệm tiền
        - hỗ trợ JSON mode / structured output
        """
        api_key = self.api_key
        if not api_key:
            logger.error("Gemini provider is not configured")
            raise AIProviderUnavailableError(
                "gemini_provider_not_configured",
                retry_after_sec=int(getattr(settings, "AI_CHAT_RETRY_AFTER_SEC", 30) or 30),
                detail="gemini_provider_not_configured",
            )

        final_system_prompt = (system_prompt or SYSTEM_REASSURANCE_PROMPT or "").strip()

        generation_config: Dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "topP": top_p,
        }

        if response_mime_type:
            generation_config["responseMimeType"] = response_mime_type

        if response_json_schema:
            generation_config["responseJsonSchema"] = response_json_schema

        payload: Dict[str, Any] = {
            "contents": [{"parts": [{"text": user_query}]}],
            "generationConfig": generation_config,
        }

        if final_system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": final_system_prompt}]}

        if use_google_search:
            # Chỉ bật khi cần real-time grounding
            payload["tools"] = [{"google_search": {}}]

        retries = 3
        delays = [1.5, 3, 6]

        client = await self._get_client()

        for i in range(retries):
            try:
                response = await client.post(
                    self.base_url,
                    json=payload,
                    headers={"x-goog-api-key": api_key},
                )

                if response.status_code == 200:
                    result = response.json()

                    candidate0 = (result.get("candidates") or [{}])[0]
                    if candidate0.get("groundingMetadata"):
                        logger.info("🌐 [AI_SEARCH] Gemini đã dùng Google Search.")

                    text = self._extract_text(result)

                    if text:
                        if use_google_search:
                            text = self._append_grounding_links(
                                text,
                                result,
                                response_mime_type=response_mime_type,
                                max_links=2,
                            )
                        return text

                    if use_google_search:
                        links = self._extract_grounding_links(result, max_links=2)
                        if links:
                            fallback = "Em chốt nhanh 2 nguồn nóng để Sếp xem trước:\n\n"
                            fallback += "\n".join([f"- {title}: {uri}" for title, uri in links])
                            return fallback

                    logger.warning("⚠️ Gemini trả về 200 nhưng text rỗng.")
                    return "Dạ, em đang phân tích nhanh cho Sếp, mình thử lại câu ngắn hơn nhé."

                body_preview = response.text[:500]
                logger.error(
                    "❌ GEMINI API ERROR (lần %s): %s - %s",
                    i + 1,
                    response.status_code,
                    body_preview,
                )

                # Lỗi auth/quota/config -> dừng luôn
                if response.status_code in (400, 401, 403):
                    raise AIProviderUnavailableError(
                        "gemini_provider_unavailable",
                        retry_after_sec=int(getattr(settings, "AI_CHAT_RETRY_AFTER_SEC", 30) or 30),
                        detail=f"gemini_http_{response.status_code}",
                    )

                # 429, 500, 503 thì retry
                if response.status_code not in (429, 500, 503):
                    raise AIProviderUnavailableError(
                        "gemini_provider_unavailable",
                        retry_after_sec=int(getattr(settings, "AI_CHAT_RETRY_AFTER_SEC", 30) or 30),
                        detail=f"gemini_http_{response.status_code}",
                    )
                if response.status_code == 429:
                    raise AIOverloadedError(
                        "gemini_overloaded",
                        retry_after_sec=int(getattr(settings, "AI_CHAT_RETRY_AFTER_SEC", 15) or 15),
                        detail="gemini_http_429",
                    )

            except httpx.TimeoutException:
                logger.error("❌ GEMINI TIMEOUT (lần %s)", i + 1)
            except Exception as e:
                logger.error("❌ LỖI KẾT NỐI GEMINI (lần %s): %s", i + 1, str(e))

            if i < retries - 1:
                await asyncio.sleep(delays[i])

        raise AIProviderUnavailableError(
            "gemini_provider_unavailable",
            retry_after_sec=int(getattr(settings, "AI_CHAT_RETRY_AFTER_SEC", 30) or 30),
            detail="gemini_request_failed",
        )


# Singleton
gemini_engine = GeminiProvider()
