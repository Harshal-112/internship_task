"""Comprehensive tests for Multi-Provider Routing & Automatic Failover (Layer 3).

Covers:
- Router default dispatch (when request.provider is omitted)
- Explicit provider dispatch ('mock', 'groq', 'ollama')
- Automatic failover when primary provider is unconfigured (missing GROQ_API_KEY)
- Automatic failover when primary provider throws connection error (Ollama offline)
- Automatic failover when primary provider throws quota/rate limit error (Groq 429)
- Multi-tier cascading failover (Groq -> Ollama -> Mock)
- Exhausted failover chain raises RuntimeError
- Provider health monitoring (get_providers_health)
- Streaming dispatch and streaming failover
- End-to-end FastAPI integration testing via TestClient (/api/v1/chat)
"""

from typing import AsyncIterator, Dict, List, Optional
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.providers.base import BaseLLMProvider, MockLLMProvider
from app.providers.groq_provider import (
    GroqAuthenticationError,
    GroqProvider,
    GroqQuotaExceededError,
)
from app.schemas.chat import (
    ChatChoice,
    ChatRequest,
    ChatResponse,
    Message,
    Role,
    TokenUsage,
)
from app.schemas.health import ProviderStatus
from app.services.router import LLMRouter, get_router


# ---------------------------------------------------------------------------
# Helper Test Mock Providers
# ---------------------------------------------------------------------------

class FailingProvider(BaseLLMProvider):
    """A provider that always raises an error on generate/stream."""

    def __init__(self, name: str, error_type: type = RuntimeError, error_msg: str = "Simulated error"):
        self._name = name
        self.error_type = error_type
        self.error_msg = error_msg

    @property
    def name(self) -> str:
        return self._name

    @property
    def default_model(self) -> str:
        return f"{self._name}-model"

    async def check_health(self) -> bool:
        return False

    async def generate(self, request: ChatRequest) -> ChatResponse:
        raise self.error_type(self.error_msg)

    async def stream_generate(self, request: ChatRequest) -> AsyncIterator[str]:
        raise self.error_type(f"Stream error on {self._name}")
        yield ""  # unreachable, makes it an async generator


class SuccessfulMockProvider(BaseLLMProvider):
    """A provider that always succeeds with custom content."""

    def __init__(self, name: str, reply: str = "Success"):
        self._name = name
        self.reply = reply

    @property
    def name(self) -> str:
        return self._name

    @property
    def default_model(self) -> str:
        return f"{self._name}-model"

    async def check_health(self) -> bool:
        return True

    async def generate(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            provider=self._name,
            model=request.model or self.default_model,
            choices=[ChatChoice(message=Message(role=Role.ASSISTANT, content=self.reply))],
            usage=TokenUsage(prompt_tokens=10, completion_tokens=15, total_tokens=25),
            latency_ms=12.5,
        )

    async def stream_generate(self, request: ChatRequest) -> AsyncIterator[str]:
        for token in self.reply.split():
            yield token + " "


@pytest.fixture
def test_client():
    """Test client for FastAPI app."""
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# Router Unit Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_router_dispatches_to_default_provider():
    """When request.provider is None, router uses default provider."""
    mock_p = SuccessfulMockProvider(name="mock", reply="Default Mock Response")
    router = LLMRouter(providers={"mock": mock_p}, default_provider="mock")

    req = ChatRequest(messages=[Message(role=Role.USER, content="Ping")])
    res = await router.route(req)

    assert res.provider == "mock"
    assert res.choices[0].message.content == "Default Mock Response"
    assert res.usage.total_tokens == 25


@pytest.mark.asyncio
async def test_router_dispatches_to_requested_provider():
    """When request.provider is specified, router dispatches to that provider."""
    mock_1 = SuccessfulMockProvider(name="prov1", reply="Response from Prov 1")
    mock_2 = SuccessfulMockProvider(name="prov2", reply="Response from Prov 2")
    router = LLMRouter(providers={"prov1": mock_1, "prov2": mock_2}, default_provider="prov1")

    req = ChatRequest(provider="prov2", messages=[Message(role=Role.USER, content="Ping")])
    res = await router.route(req)

    assert res.provider == "prov2"
    assert res.choices[0].message.content == "Response from Prov 2"


@pytest.mark.asyncio
async def test_router_failover_when_primary_fails():
    """When primary provider raises an exception, router fails over to secondary."""
    primary_failing = FailingProvider(name="groq", error_type=GroqQuotaExceededError, error_msg="Rate limit 429")
    secondary_mock = SuccessfulMockProvider(name="mock", reply="Fallback to Mock OK")

    router = LLMRouter(
        providers={"groq": primary_failing, "mock": secondary_mock},
        default_provider="groq",
        failover_order=["groq", "mock"],
    )

    req = ChatRequest(provider="groq", messages=[Message(role=Role.USER, content="Test Failover")])
    res = await router.route(req)

    # Failover must have routed to mock
    assert res.provider == "mock"
    assert res.choices[0].message.content == "Fallback to Mock OK"


@pytest.mark.asyncio
async def test_router_failover_unconfigured_groq_key():
    """When Groq API key is missing/unconfigured, router triggers automatic failover."""
    groq_no_key = GroqProvider(api_key="", fallback_to_mock=False)
    secondary_mock = SuccessfulMockProvider(name="mock", reply="Recovered via mock")

    router = LLMRouter(
        providers={"groq": groq_no_key, "mock": secondary_mock},
        default_provider="groq",
        failover_order=["groq", "mock"],
    )

    req = ChatRequest(provider="groq", messages=[Message(role=Role.USER, content="Test No Key")])
    res = await router.route(req)

    assert res.provider == "mock"
    assert res.choices[0].message.content == "Recovered via mock"


@pytest.mark.asyncio
async def test_router_cascading_failover_three_tiers():
    """Cascade: Groq fails -> Ollama fails -> Mock succeeds."""
    tier1 = FailingProvider(name="groq", error_type=GroqAuthenticationError, error_msg="Invalid API key")
    tier2 = FailingProvider(name="ollama", error_type=ConnectionRefusedError, error_msg="Ollama offline")
    tier3 = SuccessfulMockProvider(name="mock", reply="Tier 3 Mock Rescue")

    router = LLMRouter(
        providers={"groq": tier1, "ollama": tier2, "mock": tier3},
        default_provider="groq",
        failover_order=["groq", "ollama", "mock"],
    )

    req = ChatRequest(provider="groq", messages=[Message(role=Role.USER, content="Cascade test")])
    res = await router.route(req)

    assert res.provider == "mock"
    assert res.choices[0].message.content == "Tier 3 Mock Rescue"


@pytest.mark.asyncio
async def test_router_all_providers_failed_raises_runtime_error():
    """When all providers in the candidate chain fail, router raises RuntimeError."""
    p1 = FailingProvider(name="groq", error_msg="Groq down")
    p2 = FailingProvider(name="mock", error_msg="Mock down")

    router = LLMRouter(
        providers={"groq": p1, "mock": p2},
        default_provider="groq",
        failover_order=["groq", "mock"],
    )

    req = ChatRequest(provider="groq", messages=[Message(role=Role.USER, content="Doom test")])
    with pytest.raises(RuntimeError) as exc_info:
        await router.route(req)

    assert "All providers in failover chain failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_router_handles_unknown_provider_gracefully():
    """When an unregistered provider name is requested, router falls back to default/mock."""
    p_mock = SuccessfulMockProvider(name="mock", reply="Fallback for unknown provider")
    router = LLMRouter(providers={"mock": p_mock}, default_provider="mock")

    req = ChatRequest(provider="non-existent-provider-xyz", messages=[Message(role=Role.USER, content="Hi")])
    res = await router.route(req)

    assert res.provider == "mock"
    assert res.choices[0].message.content == "Fallback for unknown provider"


@pytest.mark.asyncio
async def test_router_streaming_failover():
    """Stream routing automatically fails over if primary provider stream fails."""
    p_fail = FailingProvider(name="groq", error_msg="Stream initiation broke")
    p_mock = SuccessfulMockProvider(name="mock", reply="Streamed fallback chunk")

    router = LLMRouter(
        providers={"groq": p_fail, "mock": p_mock},
        default_provider="groq",
        failover_order=["groq", "mock"],
    )

    req = ChatRequest(provider="groq", stream=True, messages=[Message(role=Role.USER, content="Stream hi")])
    chunks = []
    async for chunk in router.stream_route(req):
        chunks.append(chunk)

    combined = "".join(chunks).strip()
    assert "Streamed fallback chunk" in combined


@pytest.mark.asyncio
async def test_router_get_providers_health():
    """get_providers_health checks and reports status for all registered providers."""
    p_healthy = SuccessfulMockProvider(name="mock")
    p_unhealthy = FailingProvider(name="groq")

    router = LLMRouter(providers={"mock": p_healthy, "groq": p_unhealthy})
    health_dict = await router.get_providers_health()

    assert "mock" in health_dict
    assert health_dict["mock"].available is True
    assert "Operational" in health_dict["mock"].details

    assert "groq" in health_dict
    assert health_dict["groq"].available is False


# ---------------------------------------------------------------------------
# End-to-End API Integration Tests (/api/v1/chat)
# ---------------------------------------------------------------------------

def test_api_chat_dispatch_mock_explicit(test_client: TestClient):
    """Verify POST /api/v1/chat with provider='mock' succeeds."""
    payload = {
        "provider": "mock",
        "model": "mock-model",
        "messages": [{"role": "user", "content": "Hello from API integration"}],
    }
    headers = {"X-API-Key": "gw-test-key-1"}
    response = test_client.post("/api/v1/chat", json=payload, headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "mock"
    assert len(data["choices"]) > 0
    assert "usage" in data
    assert data["usage"]["total_tokens"] > 0
    assert "X-Request-ID" in response.headers


def test_api_chat_dispatch_groq_failover_when_no_key(test_client: TestClient, monkeypatch):
    """Verify POST /api/v1/chat with provider='groq' gracefully fails over to mock when no key is set."""
    from app.services.router import get_router
    router = get_router()
    groq_p = router.get_provider("groq")
    if groq_p:
        monkeypatch.setattr(groq_p, "_api_key", "")

    payload = {
        "provider": "groq",
        "messages": [{"role": "user", "content": "Hello Groq"}],
    }
    headers = {"X-API-Key": "gw-test-key-1"}
    response = test_client.post("/api/v1/chat", json=payload, headers=headers)

    assert response.status_code == 200
    data = response.json()
    # Response should be successfully returned (via failover to mock or ollama)
    assert data["provider"] in ("mock", "ollama")
    assert len(data["choices"]) > 0


def test_api_chat_dispatch_ollama_failover_when_offline(test_client: TestClient, monkeypatch):
    """Verify POST /api/v1/chat with provider='ollama' gracefully fails over to mock when Ollama is offline."""
    from app.services.router import get_router
    router = get_router()
    ollama_p = router.get_provider("ollama")
    if ollama_p:
        monkeypatch.setattr(ollama_p, "_base_url", "http://127.0.0.1:59999")

    payload = {
        "provider": "ollama",
        "messages": [{"role": "user", "content": "Hello Ollama"}],
    }
    headers = {"X-API-Key": "gw-test-key-1"}
    response = test_client.post("/api/v1/chat", json=payload, headers=headers)

    assert response.status_code == 200
    data = response.json()
    # Response should be successfully returned (via failover to groq or mock)
    assert data["provider"] in ("mock", "groq")
    assert len(data["choices"]) > 0


def test_api_health_endpoint_reflects_router_providers(test_client: TestClient):
    """Verify /api/v1/health reflects all providers in the router."""
    response = test_client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "healthy"
    providers = data["providers"]
    assert "mock" in providers
    assert providers["mock"]["available"] is True
    assert "ollama" in providers
    assert "groq" in providers
