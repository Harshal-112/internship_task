"""Base LLM Provider interface."""

import abc
import time
from typing import AsyncIterator, Optional
from app.schemas.chat import ChatRequest, ChatResponse, ChatChoice, Message, Role, TokenUsage


class BaseLLMProvider(abc.ABC):
    """Abstract interface that all LLM providers (Ollama, Groq, Mock) must implement."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Provider name identifier, e.g. 'ollama', 'groq', 'mock'."""
        pass

    @property
    @abc.abstractmethod
    def default_model(self) -> str:
        """Default model name for this provider."""
        pass

    @abc.abstractmethod
    async def generate(self, request: ChatRequest) -> ChatResponse:
        """Execute chat completion and return response with token usage & latency."""
        pass

    @abc.abstractmethod
    async def check_health(self) -> bool:
        """Check if provider endpoint/service is reachable and operational."""
        pass

    async def stream_generate(self, request: ChatRequest) -> AsyncIterator[str]:
        """Stream chunks of response text. Defaults to yielding whole response if not overridden."""
        resp = await self.generate(request)
        if resp.choices:
            yield resp.choices[0].message.content


class MockLLMProvider(BaseLLMProvider):
    """Deterministic, zero-dependency mock provider for offline testing & benchmarking."""

    def __init__(self, model: str = "mock-model", simulate_latency_ms: float = 20.0):
        self._model = model
        self.simulate_latency_ms = simulate_latency_ms

    @property
    def name(self) -> str:
        return "mock"

    @property
    def default_model(self) -> str:
        return self._model

    async def check_health(self) -> bool:
        return True

    async def generate(self, request: ChatRequest) -> ChatResponse:
        start_time = time.perf_counter()
        import asyncio
        if self.simulate_latency_ms > 0:
            await asyncio.sleep(self.simulate_latency_ms / 1000.0)

        # Generate a deterministic echo response
        last_msg = request.messages[-1].content if request.messages else "Hello"
        reply_text = f"[MockResponse from {self._model}]: Processed prompt: {last_msg[:80]}..."

        # Calculate approximate token counts (words * 1.3)
        prompt_words = sum(len(m.content.split()) for m in request.messages)
        prompt_tokens = max(1, int(prompt_words * 1.3))
        completion_tokens = max(1, int(len(reply_text.split()) * 1.3))
        total_tokens = prompt_tokens + completion_tokens

        latency_ms = round((time.perf_counter() - start_time) * 1000.0, 2)

        return ChatResponse(
            provider="mock",
            model=request.model or self._model,
            choices=[
                ChatChoice(
                    index=0,
                    message=Message(role=Role.ASSISTANT, content=reply_text),
                    finish_reason="stop"
                )
            ],
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens
            ),
            latency_ms=latency_ms
        )

    async def stream_generate(self, request: ChatRequest) -> AsyncIterator[str]:
        resp = await self.generate(request)
        tokens = resp.choices[0].message.content.split(" ")
        for token in tokens:
            yield token + " "
