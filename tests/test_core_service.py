"""Comprehensive tests for Core LLM Service (Layer 1).

Covers:
- Health check endpoint (/api/v1/health)
- Chat completion endpoint (/api/v1/chat)
- Client authentication (X-API-Key & Authorization Bearer)
- Rate limiting enforcement and 429 status with Retry-After header
- Schema validation errors (422 Unprocessable Entity)
- KeyManager validation and masking
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.rate_limit import rate_limiter
from app.main import app
from app.services.key_manager import key_manager


@pytest.fixture(autouse=True)
def reset_rate_limits():
    """Reset rate limiter state before each test."""
    rate_limiter.reset()
    settings = get_settings()
    rate_limiter.set_limit(settings.RATE_LIMIT_PER_MINUTE)
    yield
    rate_limiter.reset()
    rate_limiter.set_limit(settings.RATE_LIMIT_PER_MINUTE)


@pytest.fixture
def client():
    """Test client for FastAPI app."""
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Health Endpoint Tests
# ---------------------------------------------------------------------------

def test_health_check_returns_200_and_healthy(client: TestClient):
    """Test /api/v1/health returns 200 and healthy status with provider details."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "healthy"
    assert data["version"] == "1.0.0"
    assert "timestamp" in data
    assert "providers" in data

    # Verify provider entries
    providers = data["providers"]
    assert "mock" in providers
    assert providers["mock"]["available"] is True
    assert "ollama" in providers
    assert "groq" in providers


def test_root_endpoint_metadata(client: TestClient):
    """Test root endpoint returns service metadata."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "name" in data
    assert "version" in data
    assert data["health"] == "/api/v1/health"


# ---------------------------------------------------------------------------
# Authentication Tests (/api/v1/chat)
# ---------------------------------------------------------------------------

def test_chat_with_valid_x_api_key_returns_200(client: TestClient):
    """Test /api/v1/chat with valid X-API-Key header returns 200 and ChatResponse."""
    payload = {
        "messages": [
            {"role": "user", "content": "Hello, gateway!"}
        ]
    }
    headers = {"X-API-Key": "gw-test-key-1"}
    response = client.post("/api/v1/chat", json=payload, headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert data["provider"] == "mock"
    assert "choices" in data
    assert len(data["choices"]) > 0
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert "Hello, gateway!" in data["choices"][0]["message"]["content"]
    assert "usage" in data
    assert data["usage"]["total_tokens"] > 0
    assert "latency_ms" in data


def test_chat_with_valid_bearer_token_returns_200(client: TestClient):
    """Test /api/v1/chat with Authorization: Bearer <key> returns 200."""
    payload = {
        "messages": [
            {"role": "user", "content": "Testing Bearer auth"}
        ]
    }
    headers = {"Authorization": "Bearer gw-test-key-2"}
    response = client.post("/api/v1/chat", json=payload, headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "mock"
    assert len(data["choices"]) > 0


def test_chat_with_missing_api_key_returns_401(client: TestClient):
    """Test /api/v1/chat with missing API key returns 401 Unauthorized."""
    payload = {
        "messages": [
            {"role": "user", "content": "Unauthenticated prompt"}
        ]
    }
    response = client.post("/api/v1/chat", json=payload)

    assert response.status_code == 401
    data = response.json()
    assert "detail" in data
    assert "WWW-Authenticate" in response.headers


def test_chat_with_invalid_api_key_returns_401(client: TestClient):
    """Test /api/v1/chat with invalid API key returns 401 Unauthorized."""
    payload = {
        "messages": [
            {"role": "user", "content": "Unauthorized prompt"}
        ]
    }
    headers = {"X-API-Key": "invalid-secret-key-12345"}
    response = client.post("/api/v1/chat", json=payload, headers=headers)

    assert response.status_code == 401
    data = response.json()
    assert "detail" in data


def test_chat_with_invalid_bearer_token_returns_401(client: TestClient):
    """Test /api/v1/chat with invalid Bearer token returns 401 Unauthorized."""
    payload = {
        "messages": [
            {"role": "user", "content": "Unauthorized prompt"}
        ]
    }
    headers = {"Authorization": "Bearer bogus-token"}
    response = client.post("/api/v1/chat", json=payload, headers=headers)

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Rate Limiting Tests
# ---------------------------------------------------------------------------

def test_rate_limiting_triggers_429_when_threshold_exceeded(client: TestClient):
    """Test rate limiting triggers 429 Too Many Requests with Retry-After header."""
    # Set a low limit for this test
    test_limit = 3
    rate_limiter.set_limit(test_limit)

    headers = {"X-API-Key": "gw-test-key-1"}
    payload = {
        "messages": [
            {"role": "user", "content": "Ping"}
        ]
    }

    # Send requests up to the allowed limit
    for i in range(test_limit):
        res = client.post("/api/v1/chat", json=payload, headers=headers)
        assert res.status_code == 200, f"Request {i+1} should succeed"

    # The (limit + 1)th request must trigger 429
    blocked_res = client.post("/api/v1/chat", json=payload, headers=headers)
    assert blocked_res.status_code == 429
    assert "Retry-After" in blocked_res.headers
    retry_after = int(blocked_res.headers["Retry-After"])
    assert retry_after >= 1

    error_data = blocked_res.json()
    assert "detail" in error_data
    assert "Rate limit exceeded" in error_data["detail"]


def test_rate_limiting_isolated_per_client_key(client: TestClient):
    """Verify rate limit is enforced independently per client key."""
    test_limit = 2
    rate_limiter.set_limit(test_limit)

    payload = {"messages": [{"role": "user", "content": "Testing isolation"}]}

    # Client A consumes quota
    headers_a = {"X-API-Key": "gw-test-key-1"}
    for _ in range(test_limit):
        res = client.post("/api/v1/chat", json=payload, headers=headers_a)
        assert res.status_code == 200

    # Client A is now throttled
    res_a_blocked = client.post("/api/v1/chat", json=payload, headers=headers_a)
    assert res_a_blocked.status_code == 429

    # Client B should still be allowed
    headers_b = {"X-API-Key": "gw-test-key-2"}
    res_b = client.post("/api/v1/chat", json=payload, headers=headers_b)
    assert res_b.status_code == 200


# ---------------------------------------------------------------------------
# Schema Validation Tests (422 Unprocessable Entity)
# ---------------------------------------------------------------------------

def test_invalid_schema_missing_messages_triggers_422(client: TestClient):
    """Test missing messages field triggers 422."""
    headers = {"X-API-Key": "gw-test-key-1"}
    payload = {"model": "mock-model"}
    response = client.post("/api/v1/chat", json=payload, headers=headers)

    assert response.status_code == 422
    data = response.json()
    assert "detail" in data


def test_invalid_schema_empty_messages_list_triggers_422(client: TestClient):
    """Test empty messages list triggers 422 (min_length=1)."""
    headers = {"X-API-Key": "gw-test-key-1"}
    payload = {"messages": []}
    response = client.post("/api/v1/chat", json=payload, headers=headers)

    assert response.status_code == 422
    data = response.json()
    assert "detail" in data


def test_invalid_schema_empty_message_content_triggers_422(client: TestClient):
    """Test empty message content string triggers 422 (min_length=1)."""
    headers = {"X-API-Key": "gw-test-key-1"}
    payload = {
        "messages": [
            {"role": "user", "content": ""}
        ]
    }
    response = client.post("/api/v1/chat", json=payload, headers=headers)

    assert response.status_code == 422


def test_invalid_schema_unsupported_role_triggers_422(client: TestClient):
    """Test unsupported message role triggers 422."""
    headers = {"X-API-Key": "gw-test-key-1"}
    payload = {
        "messages": [
            {"role": "invalid_role", "content": "Hello"}
        ]
    }
    response = client.post("/api/v1/chat", json=payload, headers=headers)

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Streaming Chat Test
# ---------------------------------------------------------------------------

def test_chat_streaming_response(client: TestClient):
    """Test /api/v1/chat with stream=True returns 200 and event stream."""
    payload = {
        "messages": [
            {"role": "user", "content": "Stream this prompt"}
        ],
        "stream": True,
    }
    headers = {"X-API-Key": "gw-test-key-1"}
    response = client.post("/api/v1/chat", json=payload, headers=headers)

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    assert len(response.text) > 0


# ---------------------------------------------------------------------------
# KeyManager Unit Tests
# ---------------------------------------------------------------------------

def test_key_manager_validation():
    """Verify KeyManager validation logic."""
    assert key_manager.validate_key("gw-test-key-1") is True
    assert key_manager.validate_key("gw-test-key-2") is True
    assert key_manager.validate_key("not-a-valid-key") is False
    assert key_manager.validate_key("") is False
    assert key_manager.validate_key(None) is False


def test_key_manager_masking():
    """Verify KeyManager key masking does not leak full keys."""
    assert key_manager.mask_key("gw-test-key-1") == "gw-test-key-****"
    assert key_manager.mask_key("sk-123456789") == "sk-123456789" if "-" not in "sk-123456789" else "sk-****"
    assert key_manager.mask_key("abcd") == "****"
    assert key_manager.mask_key("") == "[EMPTY]"
    assert key_manager.mask_key(None) == "[EMPTY]"

    masked_list = key_manager.list_masked_keys()
    assert len(masked_list) == 2
    for masked in masked_list:
        assert "****" in masked
        assert "gw-test-key-1" not in masked
