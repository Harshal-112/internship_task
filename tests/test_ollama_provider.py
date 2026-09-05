"""Unit tests for OllamaProvider with mock transports and LangChain compatibility."""

import json
import pytest
import httpx

from app.core.config import Settings, get_settings
from app.providers.ollama_provider import AIMessageResult, OllamaProvider
from app.schemas.chat import ChatRequest, Message, Role


@pytest.fixture
def mock_settings(monkeypatch):
    settings = Settings(
        OLLAMA_BASE_URL="http://mock-ollama:11434",
        OLLAMA_DEFAULT_MODEL="qwen2.5:1.5b",
        OLLAMA_FALLBACK_MODEL="gemma2:2b",
        OLLAMA_REQUEST_TIMEOUT=10.0,
    )
    monkeypatch.setattr("app.providers.ollama_provider.get_settings", lambda: settings)
    return settings


@pytest.mark.asyncio
async def test_ollama_check_health_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:1.5b"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OllamaProvider(
            base_url="http://mock-ollama:11434",
            client=client
        )
        is_healthy = await provider.check_health()
        assert is_healthy is True


@pytest.mark.asyncio
async def test_ollama_check_health_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OllamaProvider(
            base_url="http://mock-ollama:11434",
            client=client
        )
        is_healthy = await provider.check_health()
        assert is_healthy is False


@pytest.mark.asyncio
async def test_ollama_list_models():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "models": [
                {"name": "qwen2.5:1.5b:latest"},
                {"name": "gemma2:2b:latest"}
            ]
        })

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OllamaProvider(
            base_url="http://mock-ollama:11434",
            client=client
        )
        models = await provider.list_models()
        assert len(models) == 2
        assert "qwen2.5:1.5b:latest" in models


@pytest.mark.asyncio
async def test_ollama_generate_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        req_data = json.loads(request.content)
        assert req_data["model"] == "qwen2.5:1.5b"
        assert req_data["stream"] is False

        return httpx.Response(200, json={
            "model": "qwen2.5:1.5b",
            "message": {
                "role": "assistant",
                "content": "The goat should cross first."
            },
            "done_reason": "stop",
            "total_duration": 150000000,  # 150ms
            "prompt_eval_count": 25,
            "eval_count": 12,
        })

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OllamaProvider(
            base_url="http://mock-ollama:11434",
            model="qwen2.5:1.5b",
            client=client
        )
        chat_req = ChatRequest(
            messages=[Message(role=Role.USER, content="How do I cross the river?")]
        )
        resp = await provider.generate(chat_req)

        assert resp.provider == "ollama"
        assert resp.model == "qwen2.5:1.5b"
        assert len(resp.choices) == 1
        assert resp.choices[0].message.content == "The goat should cross first."
        assert resp.usage.prompt_tokens == 25
        assert resp.usage.completion_tokens == 12
        assert resp.usage.total_tokens == 37
        assert resp.latency_ms == 150.0


@pytest.mark.asyncio
async def test_ollama_stream_generate():
    chunks = [
        json.dumps({"message": {"role": "assistant", "content": "The "}, "done": False}),
        json.dumps({"message": {"role": "assistant", "content": "farmer "}, "done": False}),
        json.dumps({"message": {"role": "assistant", "content": "crosses."}, "done": True}),
    ]
    stream_content = "\n".join(chunks).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        return httpx.Response(200, content=stream_content)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OllamaProvider(
            base_url="http://mock-ollama:11434",
            model="qwen2.5:1.5b",
            client=client
        )
        chat_req = ChatRequest(
            messages=[Message(role=Role.USER, content="Explain river puzzle")]
        )

        streamed = []
        async for chunk in provider.stream_generate(chat_req):
            streamed.append(chunk)

        assert streamed == ["The ", "farmer ", "crosses."]
        assert "".join(streamed) == "The farmer crosses."


@pytest.mark.asyncio
async def test_ollama_fallback_to_mock_on_connection_error():
    # Transport that raises ConnectError
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        # Fallback disabled -> raises
        provider_strict = OllamaProvider(
            base_url="http://down-ollama:11434",
            client=client,
            fallback_to_mock=False
        )
        chat_req = ChatRequest(
            messages=[Message(role=Role.USER, content="Hello!")]
        )
        with pytest.raises(httpx.ConnectError):
            await provider_strict.generate(chat_req)

        # Fallback enabled -> returns Mock response cleanly
        provider_fallback = OllamaProvider(
            base_url="http://down-ollama:11434",
            client=client,
            fallback_to_mock=True
        )
        resp = await provider_fallback.generate(chat_req)
        assert resp.provider == "ollama"
        assert "[MockResponse" in resp.choices[0].message.content


@pytest.mark.asyncio
async def test_langchain_ainvoke_and_invoke():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "model": "qwen2.5:1.5b",
            "message": {"role": "assistant", "content": "LangChain reply"},
            "done_reason": "stop",
            "total_duration": 50000000,
            "prompt_eval_count": 5,
            "eval_count": 4
        })

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OllamaProvider(
            base_url="http://mock-ollama:11434",
            client=client
        )

        # Test async invoke
        res_async = await provider.ainvoke("What is 2+2?")
        assert isinstance(res_async, AIMessageResult)
        assert res_async.content == "LangChain reply"
        assert str(res_async) == "LangChain reply"

        # Test sync invoke
        res_sync = provider.invoke("What is 2+2?")
        assert res_sync.content == "LangChain reply"


@pytest.mark.asyncio
async def test_langchain_astream():
    chunks = [
        json.dumps({"message": {"content": "Hello "}, "done": False}),
        json.dumps({"message": {"content": "world!"}, "done": True}),
    ]
    stream_content = "\n".join(chunks).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream_content)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OllamaProvider(
            base_url="http://mock-ollama:11434",
            client=client
        )
        results = []
        async for chunk in provider.astream("Say hello"):
            results.append(chunk)
        assert "".join(results) == "Hello world!"


def test_langchain_stream():
    chunks = [
        json.dumps({"message": {"content": "Hello "}, "done": False}),
        json.dumps({"message": {"content": "world!"}, "done": True}),
    ]
    stream_content = "\n".join(chunks).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream_content)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    provider = OllamaProvider(
        base_url="http://mock-ollama:11434",
        client=client
    )
    sync_results = list(provider.stream("Say hello"))
    assert "".join(sync_results) == "Hello world!"

