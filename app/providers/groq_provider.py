"""Groq Cloud LLM Provider implementation with official groq SDK & HTTP fallback."""

import asyncio
import logging
import os
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

try:
    from groq import (
        AsyncGroq,
        GroqError,
        RateLimitError as GroqRateLimitError,
        AuthenticationError as GroqAuthError,
        APIConnectionError as GroqConnError,
    )
    HAS_GROQ_SDK = True
except ImportError:
    HAS_GROQ_SDK = False
    AsyncGroq = None  # type: ignore
    GroqError = Exception  # type: ignore
    GroqRateLimitError = Exception  # type: ignore
    GroqAuthError = Exception  # type: ignore
    GroqConnError = Exception  # type: ignore

from app.core.config import get_settings
from app.providers.base import BaseLLMProvider, MockLLMProvider
from app.schemas.chat import (
    ChatChoice,
    ChatRequest,
    ChatResponse,
    Message,
    Role,
    TokenUsage,
)

logger = logging.getLogger(__name__)


class GroqProviderError(Exception):
    """Base exception for Groq provider failures."""
    pass


class GroqQuotaExceededError(GroqProviderError):
    """Exception raised when Groq API rate limit or free tier quota is exceeded."""
    pass


class GroqAuthenticationError(GroqProviderError):
    """Exception raised when Groq API key is invalid or missing."""
    pass


class GroqConnectionError(GroqProviderError):
    """Exception raised when unable to connect to Groq API."""
    pass


class GroqProvider(BaseLLMProvider):
    """Groq Free-tier cloud provider for ultra-fast Llama-3 inference.

    Supports:
    - Official `groq` SDK (`AsyncGroq`) with fallback to raw HTTP (`httpx`).
    - Accurate token usage extraction (`prompt_tokens`, `completion_tokens`, `total_tokens`).
    - Latency measurement (`latency_ms`).
    - Health checking via `/openai/v1/models`.
    - Graceful error handling for missing keys, quota exhaustion (429), and failover.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 30.0,
        fallback_to_mock: bool = False,
        client: Optional[Any] = None,
    ):
        settings = get_settings()
        if api_key is not None:
            self._api_key = api_key
        else:
            self._api_key = settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY")
        self._default_model = model or settings.GROQ_DEFAULT_MODEL
        self._timeout = timeout
        self._fallback_to_mock = fallback_to_mock
        self._client = client
        self._mock_provider = MockLLMProvider(
            model=self._default_model,
            simulate_latency_ms=100.0,
            name="groq",
        )

        # Initialize AsyncGroq client if available and api_key is present
        if self._client is None and HAS_GROQ_SDK and self._api_key:
            try:
                self._groq_client = AsyncGroq(api_key=self._api_key, timeout=self._timeout)
            except Exception as exc:
                logger.warning(f"Failed to initialize AsyncGroq client: {exc}")
                self._groq_client = None
        else:
            self._groq_client = self._client if (self._client and hasattr(self._client, "chat")) else None

    @property
    def name(self) -> str:
        return "groq"

    @property
    def default_model(self) -> str:
        return self._default_model

    @property
    def has_valid_key(self) -> bool:
        """Returns whether a non-empty API key is configured."""
        return bool(self._api_key and self._api_key.strip())

    async def check_health(self) -> bool:
        """Check if Groq endpoint is reachable and API key is valid."""
        if not self.has_valid_key:
            return False

        try:
            if self._groq_client is not None and hasattr(self._groq_client, "models"):
                # Use official SDK
                await self._groq_client.models.list(timeout=5.0)
                return True
            else:
                # Use httpx fallback
                client = self._client if isinstance(self._client, httpx.AsyncClient) else None
                should_close = client is None
                if client is None:
                    client = httpx.AsyncClient(timeout=5.0)
                try:
                    res = await client.get(
                        "https://api.groq.com/openai/v1/models",
                        headers={"Authorization": f"Bearer {self._api_key}"}
                    )
                    return res.status_code == 200
                finally:
                    if should_close:
                        await client.aclose()
        except (GroqAuthError, GroqRateLimitError, GroqConnError):
            return False
        except httpx.HTTPStatusError:
            return False
        except Exception as exc:
            logger.debug(f"Groq health check error: {exc}")
            return False

    async def generate(self, request: ChatRequest) -> ChatResponse:
        """Execute chat completion via Groq."""
        target_model = request.model or self._default_model

        if not self.has_valid_key:
            if self._fallback_to_mock:
                logger.info("GROQ_API_KEY not configured; using mock fallback.")
                resp = await self._mock_provider.generate(request)
                resp.provider = self.name
                resp.model = target_model
                return resp
            raise GroqAuthenticationError("GROQ_API_KEY is not configured or empty.")

        start_time = time.perf_counter()
        messages_payload = [
            {"role": m.role.value if hasattr(m.role, "value") else str(m.role), "content": m.content}
            for m in request.messages
        ]

        # 1. Try with official AsyncGroq client if available
        if self._groq_client is not None and hasattr(self._groq_client, "chat"):
            try:
                kwargs: Dict[str, Any] = {
                    "model": target_model,
                    "messages": messages_payload,
                    "temperature": request.temperature if request.temperature is not None else 0.7,
                }
                if request.max_tokens:
                    kwargs["max_tokens"] = request.max_tokens

                completion = await self._groq_client.chat.completions.create(**kwargs)
                latency_ms = round((time.perf_counter() - start_time) * 1000.0, 2)

                choice = completion.choices[0]
                content = choice.message.content or ""
                finish_reason = choice.finish_reason or "stop"
                usage = completion.usage

                return ChatResponse(
                    id=completion.id or f"chatcmpl-{int(time.time())}",
                    provider=self.name,
                    model=completion.model or target_model,
                    choices=[
                        ChatChoice(
                            index=0,
                            message=Message(role=Role.ASSISTANT, content=content),
                            finish_reason=finish_reason,
                        )
                    ],
                    usage=TokenUsage(
                        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                        total_tokens=getattr(usage, "total_tokens", 0) or 0,
                    ),
                    latency_ms=latency_ms,
                )
            except GroqRateLimitError as exc:
                logger.warning(f"Groq rate limit exceeded: {exc}")
                if self._fallback_to_mock:
                    return await self._mock_provider.generate(request)
                raise GroqQuotaExceededError(f"Groq API quota exceeded / rate limited: {exc}") from exc
            except GroqAuthError as exc:
                logger.error(f"Groq authentication error: {exc}")
                if self._fallback_to_mock:
                    return await self._mock_provider.generate(request)
                raise GroqAuthenticationError(f"Groq authentication failed: {exc}") from exc
            except Exception as exc:
                logger.error(f"Groq SDK invocation error: {exc}")
                if self._fallback_to_mock:
                    return await self._mock_provider.generate(request)
                raise GroqProviderError(f"Groq API error: {exc}") from exc

        # 2. Fallback to httpx REST client
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages_payload,
            "temperature": request.temperature if request.temperature is not None else 0.7,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens

        client = self._client if isinstance(self._client, httpx.AsyncClient) else None
        should_close = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout)

        try:
            res = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if res.status_code == 429:
                if self._fallback_to_mock:
                    logger.warning("Groq HTTP 429 Quota Exceeded; using mock fallback.")
                    return await self._mock_provider.generate(request)
                raise GroqQuotaExceededError(f"Groq API quota/rate limit exceeded (HTTP 429): {res.text}")

            if res.status_code in (401, 403):
                if self._fallback_to_mock:
                    logger.warning(f"Groq HTTP {res.status_code} Auth Error; using mock fallback.")
                    return await self._mock_provider.generate(request)
                raise GroqAuthenticationError(f"Groq API authentication failed (HTTP {res.status_code}): {res.text}")

            res.raise_for_status()
            data = res.json()

            latency_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
            content = data["choices"][0]["message"]["content"]
            usage_data = data.get("usage", {})

            return ChatResponse(
                id=data.get("id", f"chatcmpl-{int(time.time())}"),
                provider=self.name,
                model=data.get("model", target_model),
                choices=[
                    ChatChoice(
                        index=0,
                        message=Message(role=Role.ASSISTANT, content=content),
                        finish_reason=data["choices"][0].get("finish_reason", "stop"),
                    )
                ],
                usage=TokenUsage(
                    prompt_tokens=usage_data.get("prompt_tokens", 0),
                    completion_tokens=usage_data.get("completion_tokens", 0),
                    total_tokens=usage_data.get("total_tokens", 0),
                ),
                latency_ms=latency_ms,
            )
        except (GroqQuotaExceededError, GroqAuthenticationError):
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                if self._fallback_to_mock:
                    return await self._mock_provider.generate(request)
                raise GroqQuotaExceededError(f"Groq HTTP 429: {exc}") from exc
            if self._fallback_to_mock:
                return await self._mock_provider.generate(request)
            raise GroqProviderError(f"Groq HTTP status error: {exc}") from exc
        except Exception as exc:
            if self._fallback_to_mock:
                logger.warning(f"Groq call failed ({exc}); falling back to mock.")
                return await self._mock_provider.generate(request)
            raise GroqProviderError(f"Groq API request failed: {exc}") from exc
        finally:
            if should_close:
                await client.aclose()

    async def stream_generate(self, request: ChatRequest) -> AsyncIterator[str]:
        """Stream token chunks via Groq."""
        target_model = request.model or self._default_model

        if not self.has_valid_key:
            if self._fallback_to_mock:
                async for chunk in self._mock_provider.stream_generate(request):
                    yield chunk
                return
            raise GroqAuthenticationError("GROQ_API_KEY is not configured or empty.")

        messages_payload = [
            {"role": m.role.value if hasattr(m.role, "value") else str(m.role), "content": m.content}
            for m in request.messages
        ]

        # 1. Try with official AsyncGroq stream if available
        if self._groq_client is not None and hasattr(self._groq_client, "chat"):
            try:
                stream = await self._groq_client.chat.completions.create(
                    model=target_model,
                    messages=messages_payload,
                    temperature=request.temperature if request.temperature is not None else 0.7,
                    max_tokens=request.max_tokens,
                    stream=True,
                )
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
                return
            except Exception as exc:
                if self._fallback_to_mock:
                    logger.warning(f"Groq SDK stream failed ({exc}); falling back to mock stream.")
                    async for chunk in self._mock_provider.stream_generate(request):
                        yield chunk
                    return
                raise GroqProviderError(f"Groq stream error: {exc}") from exc

        # 2. Raw SSE stream via httpx
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages_payload,
            "temperature": request.temperature if request.temperature is not None else 0.7,
            "stream": True,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens

        client = self._client if isinstance(self._client, httpx.AsyncClient) else None
        should_close = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout)

        try:
            import json
            async with client.stream(
                "POST",
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as res:
                if res.status_code == 429:
                    if self._fallback_to_mock:
                        async for chunk in self._mock_provider.stream_generate(request):
                            yield chunk
                        return
                    raise GroqQuotaExceededError("Groq streaming rate limit/quota exceeded.")

                res.raise_for_status()
                async for line in res.aiter_lines():
                    line = line.strip()
                    if not line or line == "data: [DONE]":
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    try:
                        parsed = json.loads(line)
                        delta = parsed.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            yield delta
                    except Exception:
                        continue
        except Exception as exc:
            if self._fallback_to_mock:
                logger.warning(f"Groq stream failed ({exc}); falling back to mock stream.")
                async for chunk in self._mock_provider.stream_generate(request):
                    yield chunk
                return
            raise GroqProviderError(f"Groq stream failed: {exc}") from exc
        finally:
            if should_close:
                await client.aclose()
