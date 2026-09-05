"""Ollama Local LLM Provider implementation with LangChain compatibility."""

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Union

import httpx

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


class AIMessageResult(str):
    """LangChain-compatible return object that behaves as both string and message object."""

    def __new__(cls, content: str):
        obj = super().__new__(cls, content)
        obj.content = content
        return obj

    def __init__(self, content: str):
        self.content = content


class OllamaProvider(BaseLLMProvider):
    """Ollama local provider using asynchronous HTTP requests against Ollama's REST API.

    Implements:
    - BaseLLMProvider interface (`generate`, `stream_generate`, `check_health`)
    - LangChain-compatible interface (`invoke`, `ainvoke`, `stream`, `astream`)
    - Automatic Mock fallback support for offline/isolated execution environments.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        fallback_model: Optional[str] = None,
        timeout: Optional[float] = None,
        fallback_to_mock: bool = False,
        client: Optional[httpx.AsyncClient] = None,
    ):
        settings = get_settings()
        self._base_url = (base_url or settings.OLLAMA_BASE_URL).rstrip("/")
        self._default_model = model or settings.OLLAMA_DEFAULT_MODEL
        self._fallback_model = fallback_model or settings.OLLAMA_FALLBACK_MODEL
        self._timeout = timeout or settings.OLLAMA_REQUEST_TIMEOUT
        self._fallback_to_mock = fallback_to_mock
        self._client = client
        self._mock_provider = MockLLMProvider(model=self._default_model)

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def default_model(self) -> str:
        return self._default_model

    @property
    def fallback_model(self) -> str:
        return self._fallback_model

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def timeout(self) -> float:
        return self._timeout

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(timeout=self._timeout)

    async def check_health(self) -> bool:
        """Check if Ollama service is reachable and responsive at /api/tags."""
        client = self._get_client()
        should_close = self._client is None
        try:
            response = await client.get(f"{self._base_url}/api/tags", timeout=3.0)
            return response.status_code == 200
        except Exception as exc:
            logger.debug(f"Ollama health check failed: {exc}")
            return False
        finally:
            if should_close:
                await client.aclose()

    async def list_models(self) -> List[str]:
        """Query Ollama /api/tags and return available model names."""
        client = self._get_client()
        should_close = self._client is None
        try:
            response = await client.get(f"{self._base_url}/api/tags", timeout=5.0)
            response.raise_for_status()
            data = response.json()
            return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        except Exception as exc:
            logger.warning(f"Failed to list Ollama models: {exc}")
            if self._fallback_to_mock:
                return [self._default_model, self._fallback_model]
            return []
        finally:
            if should_close:
                await client.aclose()

    def _build_payload(self, request: ChatRequest, stream: bool = False) -> Dict[str, Any]:
        """Construct the request payload for Ollama /api/chat."""
        messages_payload = []
        for msg in request.messages:
            role_val = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
            messages_payload.append({
                "role": role_val,
                "content": msg.content
            })

        options: Dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = float(request.temperature)
        if request.max_tokens is not None:
            options["num_predict"] = int(request.max_tokens)

        payload: Dict[str, Any] = {
            "model": request.model or self._default_model,
            "messages": messages_payload,
            "stream": stream,
        }
        if options:
            payload["options"] = options
        return payload

    async def generate(self, request: ChatRequest) -> ChatResponse:
        """Execute chat completion via Ollama /api/chat."""
        start_time = time.perf_counter()
        target_model = request.model or self._default_model
        payload = self._build_payload(request, stream=False)

        client = self._get_client()
        should_close = self._client is None

        try:
            response = await client.post(
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=self._timeout
            )
            response.raise_for_status()
            data = response.json()

            # Latency calculation
            total_duration_ns = data.get("total_duration")
            if total_duration_ns is not None and total_duration_ns > 0:
                latency_ms = round(total_duration_ns / 1_000_000.0, 2)
            else:
                latency_ms = round((time.perf_counter() - start_time) * 1000.0, 2)

            message_content = data.get("message", {}).get("content", "")
            finish_reason = data.get("done_reason") or "stop"

            # Token usage
            prompt_tokens = data.get("prompt_eval_count")
            completion_tokens = data.get("eval_count")

            if prompt_tokens is None or completion_tokens is None:
                # Approximation fallback
                p_words = sum(len(m.content.split()) for m in request.messages)
                prompt_tokens = max(1, int(p_words * 1.3))
                completion_tokens = max(1, int(len(message_content.split()) * 1.3))

            return ChatResponse(
                provider=self.name,
                model=data.get("model", target_model),
                choices=[
                    ChatChoice(
                        index=0,
                        message=Message(role=Role.ASSISTANT, content=message_content),
                        finish_reason=finish_reason,
                    )
                ],
                usage=TokenUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                ),
                latency_ms=latency_ms,
            )
        except Exception as exc:
            if self._fallback_to_mock:
                logger.warning(
                    f"Ollama call to {self._base_url} failed ({exc}). Falling back to MockLLMProvider."
                )
                mock_resp = await self._mock_provider.generate(request)
                mock_resp.provider = self.name
                mock_resp.model = target_model
                return mock_resp
            logger.error(f"Ollama completion failed: {exc}")
            raise
        finally:
            if should_close:
                await client.aclose()

    async def stream_generate(self, request: ChatRequest) -> AsyncIterator[str]:
        """Stream token chunks via Ollama /api/chat with stream=true."""
        payload = self._build_payload(request, stream=True)
        client = self._get_client()
        should_close = self._client is None

        try:
            async with client.stream(
                "POST",
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=self._timeout
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        chunk_data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    chunk_content = chunk_data.get("message", {}).get("content", "")
                    if chunk_content:
                        yield chunk_content

                    if chunk_data.get("done", False):
                        break
        except Exception as exc:
            if self._fallback_to_mock:
                logger.warning(
                    f"Ollama stream failed ({exc}). Falling back to MockLLMProvider stream."
                )
                async for chunk in self._mock_provider.stream_generate(request):
                    yield chunk
                return
            raise
        finally:
            if should_close:
                await client.aclose()

    # --------------------------------------------------------------------------
    # LangChain-compatible interface methods
    # --------------------------------------------------------------------------

    async def ainvoke(
        self,
        input: Union[str, List[Message], ChatRequest],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> AIMessageResult:
        """Asynchronous LangChain-compatible invoke."""
        request = self._coerce_to_chat_request(input, temperature, max_tokens)
        resp = await self.generate(request)
        content = resp.choices[0].message.content if resp.choices else ""
        return AIMessageResult(content)

    def invoke(
        self,
        input: Union[str, List[Message], ChatRequest],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> AIMessageResult:
        """Synchronous LangChain-compatible invoke."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # If called inside an active asyncio loop, use thread runner or nest
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    asyncio.run,
                    self.ainvoke(input, temperature=temperature, max_tokens=max_tokens, **kwargs),
                )
                return future.result()
        else:
            return asyncio.run(
                self.ainvoke(input, temperature=temperature, max_tokens=max_tokens, **kwargs)
            )

    async def astream(
        self,
        input: Union[str, List[Message], ChatRequest],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Asynchronous LangChain-compatible stream."""
        request = self._coerce_to_chat_request(input, temperature, max_tokens)
        async for chunk in self.stream_generate(request):
            yield chunk

    def stream(
        self,
        input: Union[str, List[Message], ChatRequest],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        """Synchronous LangChain-compatible stream."""
        import queue
        import threading

        q: queue.Queue = queue.Queue()
        sentinel = object()

        def _worker():
            async def _run():
                try:
                    async for item in self.astream(
                        input, temperature=temperature, max_tokens=max_tokens, **kwargs
                    ):
                        q.put(item)
                except Exception as e:
                    q.put(e)
                finally:
                    q.put(sentinel)

            # Use a fresh event loop in worker thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

        while True:
            item = q.get()
            if item is sentinel:
                break
            if isinstance(item, Exception):
                raise item
            yield item


    def _coerce_to_chat_request(
        self,
        input: Union[str, List[Message], ChatRequest],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> ChatRequest:
        """Helper to convert various input formats to ChatRequest."""
        if isinstance(input, ChatRequest):
            req = input
            if temperature is not None:
                req.temperature = temperature
            if max_tokens is not None:
                req.max_tokens = max_tokens
            return req

        messages: List[Message] = []
        if isinstance(input, str):
            messages = [Message(role=Role.USER, content=input)]
        elif isinstance(input, list):
            for item in input:
                if isinstance(item, Message):
                    messages.append(item)
                elif isinstance(item, dict):
                    messages.append(Message(role=item.get("role", Role.USER), content=item.get("content", "")))
                elif hasattr(item, "content"):
                    role = getattr(item, "type", "user")
                    messages.append(Message(role=role, content=item.content))
                else:
                    messages.append(Message(role=Role.USER, content=str(item)))
        else:
            messages = [Message(role=Role.USER, content=str(input))]

        return ChatRequest(
            model=self._default_model,
            messages=messages,
            temperature=temperature if temperature is not None else 0.7,
            max_tokens=max_tokens,
        )
