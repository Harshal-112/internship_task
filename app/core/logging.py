"""Structured JSON logging and secret redaction module for Multi-Provider Gateway."""

import copy
import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Union

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings

logger = logging.getLogger("gateway.observability")


# ---------------------------------------------------------------------------
# Secret Redaction Utility
# ---------------------------------------------------------------------------

# Sensitive dictionary keys to redact
SENSITIVE_KEY_NAMES: Set[str] = {
    "authorization",
    "x-api-key",
    "api_key",
    "apikey",
    "key",
    "secret",
    "password",
    "token",
    "access_token",
    "groq_api_key",
}

# Regex patterns for secret tokens
BEARER_PATTERN = re.compile(r"Bearer\s+([A-Za-z0-9_\-\.]+)", re.IGNORECASE)
GROQ_KEY_PATTERN = re.compile(r"gsk_[A-Za-z0-9]{10,}", re.IGNORECASE)
GW_KEY_PATTERN = re.compile(r"gw-[A-Za-z0-9_\-]{4,}", re.IGNORECASE)


def get_known_secrets() -> List[str]:
    """Retrieve known configured secrets from settings for dynamic redaction."""
    settings = get_settings()
    secrets: List[str] = []

    # Add GROQ_API_KEY if present
    if settings.GROQ_API_KEY and settings.GROQ_API_KEY.strip():
        secrets.append(settings.GROQ_API_KEY.strip())

    # Add GATEWAY_API_KEYS
    raw_keys = settings.GATEWAY_API_KEYS
    if isinstance(raw_keys, str):
        keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    else:
        keys = list(raw_keys)

    for k in keys:
        if k and len(k) >= 4:
            secrets.append(k)

    return secrets


def redact_string(text: str, custom_secrets: Optional[List[str]] = None) -> str:
    """Redact secrets, Bearer tokens, and API keys from a string."""
    if not text:
        return text

    # Redact Bearer tokens
    text = BEARER_PATTERN.sub("Bearer [REDACTED]", text)

    # Redact Groq API key patterns (gsk_...)
    text = GROQ_KEY_PATTERN.sub("[REDACTED_GROQ_KEY]", text)

    # Redact known static secrets from settings
    known = get_known_secrets()
    if custom_secrets:
        known.extend(custom_secrets)

    for secret in known:
        if secret and len(secret) >= 4 and secret in text:
            text = text.replace(secret, "[REDACTED_SECRET]")

    # Redact general gateway key pattern (gw-...)
    text = GW_KEY_PATTERN.sub("[REDACTED_KEY]", text)

    return text


def redact_secrets(data: Any, custom_secrets: Optional[List[str]] = None) -> Any:
    """Recursively redact sensitive keys and token values from dicts, lists, and strings."""
    if isinstance(data, str):
        return redact_string(data, custom_secrets)

    if isinstance(data, dict):
        sanitized_dict: Dict[str, Any] = {}
        for k, v in data.items():
            k_lower = str(k).lower().strip()
            if k_lower in SENSITIVE_KEY_NAMES:
                sanitized_dict[k] = "[REDACTED]"
            elif isinstance(v, (dict, list)):
                sanitized_dict[k] = redact_secrets(v, custom_secrets)
            elif isinstance(v, str):
                sanitized_dict[k] = redact_string(v, custom_secrets)
            else:
                sanitized_dict[k] = v
        return sanitized_dict

    if isinstance(data, list):
        return [redact_secrets(item, custom_secrets) for item in data]

    if isinstance(data, tuple):
        return tuple(redact_secrets(item, custom_secrets) for item in data)

    return data


# ---------------------------------------------------------------------------
# Structured JSON Formatter
# ---------------------------------------------------------------------------

# Standard logging record attributes to omit from extras
RESERVED_RECORD_ATTRS: Set[str] = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}

# The 10 required telemetry fields
TELEMETRY_FIELDS: List[str] = [
    "timestamp",
    "request_id",
    "client_id",
    "provider",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "latency_ms",
    "status_code",
]


class StructuredJSONFormatter(logging.Formatter):
    """Format log records as structured single-line JSON with telemetry and secret redaction."""

    def __init__(self, redact: bool = True):
        super().__init__()
        self.redact = redact

    def format(self, record: logging.LogRecord) -> str:
        # Base structured fields
        timestamp = datetime.now(timezone.utc).isoformat()
        log_obj: Dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include telemetry fields if present on record
        for field in TELEMETRY_FIELDS:
            if field == "timestamp":
                continue
            if hasattr(record, field):
                log_obj[field] = getattr(record, field)

        # Include any extra non-reserved attributes
        for key, val in record.__dict__.items():
            if key not in RESERVED_RECORD_ATTRS and key not in log_obj:
                log_obj[key] = val

        # Include exception info if present
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        # Apply secret redaction
        settings = get_settings()
        should_redact = self.redact and getattr(settings, "REDACT_SECRETS_IN_LOGS", True)
        if should_redact:
            log_obj = redact_secrets(log_obj)

        return json.dumps(log_obj, default=str)


# ---------------------------------------------------------------------------
# Telemetry Logging Helper
# ---------------------------------------------------------------------------

def log_telemetry_event(
    request_id: str,
    client_id: Optional[str],
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    latency_ms: float,
    status_code: int,
    error: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Emit a structured telemetry log event with all 10 core fields."""
    timestamp = datetime.now(timezone.utc).isoformat()
    telemetry_payload: Dict[str, Any] = {
        "timestamp": timestamp,
        "request_id": request_id,
        "client_id": client_id,
        "provider": provider,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "latency_ms": round(float(latency_ms), 2),
        "status_code": status_code,
    }

    if error:
        telemetry_payload["error"] = error
    if extra:
        telemetry_payload.update(extra)

    # Log with extra kwargs so the StructuredJSONFormatter includes them
    msg = f"Chat completion handled by '{provider}' ({model}) - Status: {status_code} - Latency: {latency_ms:.2f}ms"
    logger.info(msg, extra=telemetry_payload)

    return telemetry_payload


# ---------------------------------------------------------------------------
# FastAPI Middleware for Request/Response Interception
# ---------------------------------------------------------------------------

class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """Intercepts incoming requests and responses to record request ID, timing, and structured logs."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.perf_counter()

        # Extract or generate request ID
        req_id = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4().hex[:12]}"
        request.state.request_id = req_id

        # Pass request to next handler
        response = None
        error_msg = None
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            # Append X-Request-ID header to response
            response.headers["X-Request-ID"] = req_id
            return response
        except Exception as exc:
            error_msg = str(exc)
            raise exc
        finally:
            latency_ms = round((time.perf_counter() - start_time) * 1000.0, 2)

            # Only emit full chat telemetry for chat completion endpoints
            if request.url.path.startswith("/api/v1/chat"):
                client_id = getattr(request.state, "client_id", None) or getattr(request.state, "masked_key", None)
                provider = getattr(request.state, "provider", "unknown")
                model = getattr(request.state, "model", "unknown")
                usage = getattr(request.state, "usage", None)

                prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
                completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
                total_tokens = getattr(usage, "total_tokens", 0) if usage else 0

                log_telemetry_event(
                    request_id=req_id,
                    client_id=client_id,
                    provider=provider,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    latency_ms=latency_ms,
                    status_code=status_code,
                    error=error_msg,
                )


# ---------------------------------------------------------------------------
# Logging Setup Initializer
# ---------------------------------------------------------------------------

def setup_logging(level: Optional[str] = None) -> None:
    """Configure structured JSON logging handler on root and gateway loggers."""
    settings = get_settings()
    log_level_str = (level or settings.LOG_LEVEL).upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    root_logger = logging.getLogger()
    gateway_logger = logging.getLogger("gateway")

    formatter = StructuredJSONFormatter(redact=settings.REDACT_SECRETS_IN_LOGS)

    # Avoid duplicate handlers
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.setLevel(log_level)

    # Clear existing handlers on gateway logger
    gateway_logger.handlers.clear()
    gateway_logger.addHandler(handler)
    gateway_logger.setLevel(log_level)
    gateway_logger.propagate = False
