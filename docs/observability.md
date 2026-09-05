# Gateway Observability & Structured Logging Specification

**Module**: Multi-Provider Routing & Observability (Layer 3 - L3-08)  
**Version**: 1.0.0  
**Status**: Production Ready  

---

## 1. Overview

The Secure Multi-Provider LLM Gateway implements high-performance, structured JSON logging and end-to-end telemetry. Every request processed by the gateway produces machine-readable, indexable JSON log records compatible with modern log aggregators (e.g., Datadog, Grafana Loki, AWS CloudWatch, ElasticSearch/Kibana).

Key capabilities:
- **Comprehensive Telemetry**: Tracks 10 standardized fields for every chat completion.
- **Automated Secret Redaction**: Zero secret leakage for Groq API keys, client gateway API keys, and Authorization Bearer tokens.
- **Trace Context Propagation**: Automatically generates or propagates unique `X-Request-ID` across middleware and service boundaries.
- **Performance Timing**: High-resolution latency profiling (`time.perf_counter()`) measuring exact request duration down to hundredths of a millisecond.

---

## 2. Telemetry Schema & Fields

Every chat completion event logged by the gateway adheres to the following JSON schema:

| Field Name | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `timestamp` | string (ISO 8601 UTC) | Exact UTC timestamp of log generation | `"2026-09-05T05:00:15.123456+00:00"` |
| `request_id` | string | Unique request identifier (`X-Request-ID`) | `"req-8f4e2b10a9c3"` |
| `client_id` | string (or null) | Masked client identification key | `"gw-test-key-****"` |
| `provider` | string | LLM provider fulfilling completion | `"groq"`, `"ollama"`, `"mock"` |
| `model` | string | Target model utilized | `"llama-3.1-8b-instant"` |
| `prompt_tokens` | integer | Tokens consumed in prompt/messages | `24` |
| `completion_tokens` | integer | Tokens generated in response | `48` |
| `total_tokens` | integer | Sum of prompt and completion tokens | `72` |
| `latency_ms` | float | Round-trip request execution duration in ms | `142.50` |
| `status_code` | integer | HTTP status code returned to client | `200` |

### Optional Context Fields

| Field Name | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `level` | string | Standard logging severity level | `"INFO"`, `"WARNING"`, `"ERROR"` |
| `logger` | string | Logger module name | `"gateway.observability"` |
| `error` | string (optional) | Error message if failover or failure occurred | `"GroqQuotaExceededError: 429"` |
| `message` | string | Human-readable log summary | `"Chat completion handled by 'groq'"` |

---

## 3. Sample Log Records

### 3.1 Successful Cloud Completion (Groq Free Tier)

```json
{
  "timestamp": "2026-09-05T05:01:22.451892+00:00",
  "level": "INFO",
  "logger": "gateway.observability",
  "message": "Chat completion handled by 'groq' (llama-3.1-8b-instant) - Status: 200 - Latency: 135.20ms",
  "request_id": "req-9a1b2c3d4e5f",
  "client_id": "gw-test-key-****",
  "provider": "groq",
  "model": "llama-3.1-8b-instant",
  "prompt_tokens": 18,
  "completion_tokens": 52,
  "total_tokens": 70,
  "latency_ms": 135.2,
  "status_code": 200
}
```

### 3.2 Automatic Failover Event (Groq Quota Exceeded -> Local Ollama / Mock)

```json
{
  "timestamp": "2026-09-05T05:02:10.114782+00:00",
  "level": "INFO",
  "logger": "gateway.observability",
  "message": "Chat completion handled by 'mock' (mock-model) - Status: 200 - Latency: 15.40ms",
  "request_id": "req-112233445566",
  "client_id": "gw-test-key-****",
  "provider": "mock",
  "model": "mock-model",
  "prompt_tokens": 15,
  "completion_tokens": 20,
  "total_tokens": 35,
  "latency_ms": 15.4,
  "status_code": 200,
  "fallback_reason": "GroqQuotaExceededError: HTTP 429 Quota Exceeded"
}
```

### 3.3 Authentication Error Event (HTTP 401)

```json
{
  "timestamp": "2026-09-05T05:03:05.892110+00:00",
  "level": "WARNING",
  "logger": "gateway.security",
  "message": "Client authentication failed: invalid API key provided",
  "request_id": "req-bbccddeeff00",
  "client_id": null,
  "provider": "unknown",
  "model": "unknown",
  "prompt_tokens": 0,
  "completion_tokens": 0,
  "total_tokens": 0,
  "latency_ms": 1.25,
  "status_code": 401
}
```

---

## 4. Secret Redaction Policies

To ensure compliance with security standards (SOC 2, ISO 27001, GDPR), the gateway strictly enforces automatic secret sanitization before logs reach stdout, files, or remote collectors.

### 4.1 Redaction Rules

1. **Groq Cloud API Keys**:
   - Pattern: `gsk_[A-Za-z0-9]{10,}`
   - Replaced with: `[REDACTED_GROQ_KEY]`
   - Configured `GROQ_API_KEY` environment values are dynamically scrubbed.

2. **Client Gateway Keys**:
   - Keys in `GATEWAY_API_KEYS` (e.g. `gw-test-key-1`).
   - Pattern: `gw-[A-Za-z0-9_\-]{4,}`
   - Replaced with: `[REDACTED_SECRET]` or `[REDACTED_KEY]`

3. **HTTP Authorization Headers**:
   - Pattern: `Bearer\s+([A-Za-z0-9_\-\.]+)`
   - Replaced with: `Bearer [REDACTED]`

4. **Sensitive Payload & Dictionary Keys**:
   - Keys: `authorization`, `x-api-key`, `api_key`, `apikey`, `secret`, `password`, `token`, `access_token`
   - Values replaced with: `"[REDACTED]"`
   - Evaluated recursively across nested dictionaries, arrays, and tuples.

### 4.2 Configuration

Redaction can be configured in `.env`:
```ini
ENABLE_STRUCTURED_LOGGING=true
LOG_LEVEL="INFO"
REDACT_SECRETS_IN_LOGS=true
```

---

## 5. Integration Architecture

```
                       HTTP Request
                            │
                            ▼
             ┌──────────────────────────────┐
             │ StructuredLoggingMiddleware  │
             │ - Assigns req-UUID / header  │
             │ - Records start timestamp    │
             └──────────────┬───────────────┘
                            │
                            ▼
             ┌──────────────────────────────┐
             │ Authentication & Rate Limit  │
             │ - Validates client key       │
             │ - Records masked client_id   │
             └──────────────┬───────────────┘
                            │
                            ▼
             ┌──────────────────────────────┐
             │         LLMRouter            │
             │ - Primary provider dispatch  │
             │ - Automatic failover chain   │
             │ - Records usage & provider   │
             └──────────────┬───────────────┘
                            │
                            ▼
             ┌──────────────────────────────┐
             │ StructuredJSONFormatter      │
             │ - Extracts 10 telemetry vars │
             │ - Applies secret redaction   │
             │ - Emits single-line JSON     │
             └──────────────────────────────┘
```

---

## 6. Verification and Health Auditing

Querying the health endpoint exposes the availability status of all integrated providers:

```bash
curl -X GET http://127.0.0.1:8000/api/v1/health
```

Example JSON response:
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "timestamp": 1725512400.0,
  "providers": {
    "mock": {
      "name": "mock",
      "available": true,
      "details": "Operational (mock provider ready)"
    },
    "ollama": {
      "name": "ollama",
      "available": false,
      "details": "Ollama local service unreachable"
    },
    "groq": {
      "name": "groq",
      "available": false,
      "details": "GROQ_API_KEY not configured or cloud endpoint unreachable"
    }
  }
}
```
