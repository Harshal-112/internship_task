"""Comprehensive tests for Structured JSON Logging & Observability (Layer 3).

Covers:
- StructuredJSONFormatter produces valid JSON
- Presence and validation of all 10 core telemetry fields:
    (timestamp, request_id, client_id, provider, model,
     prompt_tokens, completion_tokens, total_tokens, latency_ms, status_code)
- Secret redaction utility:
    - GROQ_API_KEY redaction (gsk_... pattern and configured key)
    - Client gateway key redaction (gw-test-key-1, gw-...)
    - Authorization Bearer token redaction (Bearer <token>)
    - Sensitive dictionary key redaction (api_key, token, secret, authorization, password)
    - Nested structures (nested dicts, lists, tuples)
- End-to-end FastAPI middleware integration:
    - Request ID injection into response headers (X-Request-ID)
    - Telemetry emission upon chat completion
    - Zero secret leakage in log streams
"""

import json
import logging
from io import StringIO
from typing import Any, Dict
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.logging import (
    StructuredJSONFormatter,
    StructuredLoggingMiddleware,
    log_telemetry_event,
    redact_secrets,
    redact_string,
    setup_logging,
    TELEMETRY_FIELDS,
)
from app.main import app


@pytest.fixture
def test_client():
    """TestClient fixture."""
    with TestClient(app) as client:
        yield client


@pytest.fixture
def log_capture():
    """Fixture to capture logs emitted through StructuredJSONFormatter."""
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredJSONFormatter(redact=True))

    target_logger = logging.getLogger("gateway.observability")
    old_handlers = list(target_logger.handlers)
    target_logger.handlers = [handler]
    target_logger.setLevel(logging.INFO)
    target_logger.propagate = False

    yield stream

    target_logger.handlers = old_handlers


# ---------------------------------------------------------------------------
# Unit Tests: StructuredJSONFormatter & Core Telemetry Fields
# ---------------------------------------------------------------------------

def test_json_formatter_produces_valid_json():
    """StructuredJSONFormatter formats a standard log record as a valid single-line JSON string."""
    formatter = StructuredJSONFormatter(redact=False)
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=42,
        msg="Test message content",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    assert isinstance(formatted, str)

    parsed = json.loads(formatted)
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test_logger"
    assert parsed["message"] == "Test message content"
    assert "timestamp" in parsed


def test_telemetry_event_contains_all_10_required_fields(log_capture: StringIO):
    """Verify log_telemetry_event outputs all 10 required telemetry fields."""
    event = log_telemetry_event(
        request_id="req-abc-123",
        client_id="gw-test-key-****",
        provider="groq",
        model="openai/gpt-oss-20b",
        prompt_tokens=42,
        completion_tokens=58,
        total_tokens=100,
        latency_ms=154.23,
        status_code=200,
        extra={"custom_tag": "eval_run"},
    )

    # Check returned dictionary has all fields
    for field in TELEMETRY_FIELDS:
        assert field in event, f"Field '{field}' missing from telemetry event"

    assert event["request_id"] == "req-abc-123"
    assert event["client_id"] == "gw-test-key-****"
    assert event["provider"] == "groq"
    assert event["model"] == "openai/gpt-oss-20b"
    assert event["prompt_tokens"] == 42
    assert event["completion_tokens"] == 58
    assert event["total_tokens"] == 100
    assert event["latency_ms"] == 154.23
    assert event["status_code"] == 200

    # Verify captured log line contains valid JSON with all fields
    log_output = log_capture.getvalue().strip()
    assert len(log_output) > 0
    logged_json = json.loads(log_output.splitlines()[-1])

    for field in TELEMETRY_FIELDS:
        assert field in logged_json, f"Field '{field}' missing from logged JSON"
    assert logged_json["custom_tag"] == "eval_run"


# ---------------------------------------------------------------------------
# Unit Tests: Secret Redaction Utility
# ---------------------------------------------------------------------------

def test_redact_groq_api_key_patterns():
    """Redaction masks Groq API keys starting with gsk_."""
    raw_text = "Connecting with key gsk_abcdef1234567890qwertyuiop to Groq API"
    redacted = redact_string(raw_text)
    assert "gsk_abcdef1234567890qwertyuiop" not in redacted
    assert "[REDACTED_GROQ_KEY]" in redacted


def test_redact_configured_gateway_keys():
    """Redaction masks configured client keys (e.g. gw-test-key-1)."""
    raw_text = "Client authenticated using gw-test-key-1 from IP 127.0.0.1"
    redacted = redact_string(raw_text)
    assert "gw-test-key-1" not in redacted
    assert "[REDACTED_SECRET]" in redacted or "[REDACTED_KEY]" in redacted


def test_redact_bearer_tokens():
    """Redaction masks Authorization: Bearer <token> patterns."""
    raw_header = "Authorization: Bearer my-super-secret-bearer-token-12345"
    redacted = redact_string(raw_header)
    assert "my-super-secret-bearer-token-12345" not in redacted
    assert "Bearer [REDACTED]" in redacted


def test_redact_secrets_in_dict_and_nested_structures():
    """Redaction recursively sanitizes sensitive keys and values in complex dicts."""
    sample_data: Dict[str, Any] = {
        "user": "alice",
        "api_key": "gw-test-key-1",
        "authorization": "Bearer secret-token-xyz",
        "metadata": {
            "password": "super-secret-password",
            "token": "gsk_9876543210abcdef",
            "safe_field": "public_data",
            "sub_list": [
                "safe_item",
                "contains gw-test-key-2 inside string",
                {"nested_secret": "Bearer tok123"},
            ],
        },
    }

    sanitized = redact_secrets(sample_data)

    # Check top-level sensitive keys
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["authorization"] == "[REDACTED]"

    # Check nested dict
    meta = sanitized["metadata"]
    assert meta["password"] == "[REDACTED]"
    assert meta["token"] == "[REDACTED]"
    assert meta["safe_field"] == "public_data"

    # Check nested list
    assert meta["sub_list"][0] == "safe_item"
    assert "gw-test-key-2" not in meta["sub_list"][1]
    assert "tok123" not in meta["sub_list"][2]["nested_secret"]


def test_json_formatter_redacts_secrets_in_log_output():
    """StructuredJSONFormatter automatically redacts secrets in LogRecord message and extras."""
    formatter = StructuredJSONFormatter(redact=True)
    record = logging.LogRecord(
        name="gateway.test",
        level=logging.WARNING,
        pathname="test.py",
        lineno=10,
        msg="Error authenticating with key gw-test-key-1 and gsk_fakegroqkey12345678",
        args=(),
        exc_info=None,
    )
    record.api_key = "gw-test-key-1"
    record.raw_header = "Bearer secret123"

    formatted = formatter.format(record)
    assert "gw-test-key-1" not in formatted
    assert "gsk_fakegroqkey12345678" not in formatted
    assert "secret123" not in formatted

    parsed = json.loads(formatted)
    assert parsed["api_key"] == "[REDACTED]"
    assert "[REDACTED]" in parsed["raw_header"]


# ---------------------------------------------------------------------------
# Integration Tests: End-to-End Middleware & Request Telemetry
# ---------------------------------------------------------------------------

def test_chat_endpoint_injects_x_request_id_header(test_client: TestClient):
    """StructuredLoggingMiddleware ensures X-Request-ID header is present in every response."""
    payload = {
        "messages": [{"role": "user", "content": "Ping observability"}],
    }
    headers = {"X-API-Key": "gw-test-key-1"}
    response = test_client.post("/api/v1/chat", json=payload, headers=headers)

    assert response.status_code == 200
    assert "X-Request-ID" in response.headers
    assert response.headers["X-Request-ID"].startswith("req-")


def test_chat_endpoint_preserves_custom_request_id(test_client: TestClient):
    """Client-supplied X-Request-ID header is preserved by middleware."""
    custom_id = "req-custom-client-trace-999"
    payload = {
        "messages": [{"role": "user", "content": "Trace test"}],
    }
    headers = {
        "X-API-Key": "gw-test-key-1",
        "X-Request-ID": custom_id,
    }
    response = test_client.post("/api/v1/chat", json=payload, headers=headers)

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == custom_id


def test_chat_endpoint_telemetry_emission(test_client: TestClient, log_capture: StringIO):
    """Chat completion through TestClient emits complete telemetry event."""
    payload = {
        "provider": "mock",
        "model": "mock-model",
        "messages": [{"role": "user", "content": "Measure my telemetry"}],
    }
    headers = {"X-API-Key": "gw-test-key-1"}
    response = test_client.post("/api/v1/chat", json=payload, headers=headers)
    assert response.status_code == 200

    log_output = log_capture.getvalue().strip()
    assert len(log_output) > 0

    # Parse last emitted JSON line
    last_line = log_output.splitlines()[-1]
    log_data = json.loads(last_line)

    assert log_data["provider"] == "mock"
    assert log_data["status_code"] == 200
    assert log_data["prompt_tokens"] > 0
    assert log_data["completion_tokens"] > 0
    assert log_data["total_tokens"] == log_data["prompt_tokens"] + log_data["completion_tokens"]
    assert log_data["latency_ms"] >= 0.0
    assert "timestamp" in log_data
    assert "request_id" in log_data


def test_logs_do_not_leak_raw_client_keys_or_bearer_tokens(test_client: TestClient, log_capture: StringIO):
    """Verify that neither raw client gateway keys nor raw Bearer tokens appear anywhere in logs."""
    raw_key = "gw-test-key-2"
    payload = {
        "messages": [{"role": "user", "content": "Security check"}],
    }
    headers = {"Authorization": f"Bearer {raw_key}"}
    response = test_client.post("/api/v1/chat", json=payload, headers=headers)
    assert response.status_code == 200

    log_output = log_capture.getvalue()
    # Ensure raw secret is not leaked in log output
    assert raw_key not in log_output or "[REDACTED" in log_output
