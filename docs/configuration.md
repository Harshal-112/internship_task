# Configuration & Security Guide

This document details the configuration options, client authentication mechanisms, rate-limiting policies, and security practices for the **Secure Multi-Provider LLM Gateway**.

---

## 1. Overview & Architecture

The gateway serves as a unified, secure proxy for Large Language Model (LLM) providers (Local Ollama, Cloud Groq, and Mock fallback). It implements:

- **Static Client Authentication**: API Key validation via `X-API-Key` or `Authorization: Bearer <token>`.
- **Sliding-Window Rate Limiting**: In-memory, per-client or per-IP quota enforcement with dynamic `Retry-After` headers.
- **Provider Abstraction**: Common schemas (`/api/v1/chat`, `/api/v1/health`) adhering to OpenAI-compatible standards.
- **Secret Redaction & Key Masking**: Safe logging practices that prevent API key leakage.

---

## 2. Environment Variables Reference

Configuration is managed via Pydantic Settings (`app/core/config.py`) and loaded from environment variables or a local `.env` file.

| Variable | Type | Default | Description |
|---|---|---|---|
| `APP_NAME` | `str` | `"Secure Multi-Provider LLM Gateway"` | Name of the gateway service. |
| `APP_ENV` | `str` | `"development"` | Runtime environment (`development`, `production`, `test`). |
| `DEBUG` | `bool` | `False` | Enable debug logs and hot reload. |
| `HOST` | `str` | `"127.0.0.1"` | Host address for Uvicorn server. |
| `PORT` | `int` | `8000` | Port for HTTP service. |
| **`GATEWAY_API_KEYS`** | `str` / `list` | `["gw-test-key-1", "gw-test-key-2"]` | Comma-separated list of authorized client API keys for gateway access. |
| **`RATE_LIMIT_PER_MINUTE`** | `int` | `60` | Maximum allowed requests per sliding 60-second window per client. |
| `OLLAMA_BASE_URL` | `str` | `"http://localhost:11434"` | Endpoint URL for local Ollama instance (Layer 2). |
| `OLLAMA_DEFAULT_MODEL` | `str` | `"qwen2.5:1.5b"` | Primary local LLM model (optimized for 8GB RAM host). |
| `OLLAMA_FALLBACK_MODEL`| `str` | `"gemma2:2b"` | Fallback local LLM model. |
| `OLLAMA_REQUEST_TIMEOUT`| `float`| `60.0` | Timeout in seconds for Ollama requests. |
| `GROQ_API_KEY` | `str` (Optional)| `None` | API key for Groq Cloud API (Layer 3). |
| `GROQ_DEFAULT_MODEL` | `str` | `"llama-3.1-8b-instant"` | Default Groq model for cloud acceleration. |
| `DEFAULT_PROVIDER` | `str` | `"mock"` | Default fallback provider (`mock`, `ollama`, `groq`). |
| `ENABLE_STRUCTURED_LOGGING`| `bool`| `True` | Output logs in structured JSON format. |
| `LOG_LEVEL` | `str` | `"INFO"` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `REDACT_SECRETS_IN_LOGS` | `bool` | `True` | Automatically redact sensitive tokens and keys in log outputs. |

---

## 3. Client Authentication Setup

All requests to `/api/v1/chat` require a valid client API key configured in `GATEWAY_API_KEYS`.

### 3.1 Passing the API Key

Clients may supply their API key using either of two standard header conventions:

#### Option A: `X-API-Key` Header (Recommended for scripts & SDKs)
```http
POST /api/v1/chat HTTP/1.1
Host: 127.0.0.1:8000
Content-Type: application/json
X-API-Key: gw-test-key-1

{
  "messages": [{"role": "user", "content": "Hello!"}]
}
```

#### Option B: `Authorization: Bearer <key>` Header (Standard Bearer Token)
```http
POST /api/v1/chat HTTP/1.1
Host: 127.0.0.1:8000
Content-Type: application/json
Authorization: Bearer gw-test-key-1

{
  "messages": [{"role": "user", "content": "Hello!"}]
}
```

### 3.2 Authentication Error Responses

If a request lacks an API key or provides an unrecognized key, the gateway responds with HTTP 401 Unauthorized:

```json
{
  "detail": "Invalid or missing API key. Provide a valid key via 'X-API-Key' or 'Authorization: Bearer <key>'."
}
```
Response header: `WWW-Authenticate: Bearer`

---

## 4. Rate Limiting

The gateway enforces rate limiting using a **Thread-Safe In-Memory Sliding-Window** algorithm (`app/core/rate_limit.py`).

### 4.1 How It Works

1. **Client Identity Extraction**:
   - Authenticated client requests are tracked by their API key (`key:<api-key>`).
   - Unauthenticated endpoints or requests falling back to IP are tracked by host IP (`ip:<host>`).
2. **Sliding Window Tracking**:
   - Exact timestamps of successful requests within the last 60 seconds are retained.
   - Timestamps older than `current_time - 60s` are pruned on each arrival.
3. **Threshold Enforcement**:
   - If the number of requests within the window reaches `RATE_LIMIT_PER_MINUTE`, the request is immediately rejected.
4. **`Retry-After` Calculation**:
   - The gateway calculates the exact number of seconds until the oldest request timestamp rolls out of the 60-second window:
     $$\text{Retry-After} = \max(1, \lceil 60 - (\text{now} - \text{oldest\_timestamp}) \rceil)$$
   - The `Retry-After` header is included in the HTTP response.

### 4.2 Rate Limit Exceeded Response (HTTP 429)

```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/json
Retry-After: 14

{
  "detail": "Rate limit exceeded: maximum 60 requests per minute allowed."
}
```

---

## 5. Key Masking & Security

To prevent secret leakage in logs, traces, or monitoring dashboards:

- `KeyManager.mask_key()` (`app/services/key_manager.py`) transforms keys into safe representations:
  - `gw-test-key-1` $\rightarrow$ `gw-test-key-****`
  - `sk-proj-987654321` $\rightarrow$ `sk-proj-****`
  - Short tokens ($\le 4$ chars) $\rightarrow$ `****`
- Request contexts store `request.state.masked_key` for safe logging by application loggers and middleware.
- Cleartext API keys are never stored in log records when `REDACT_SECRETS_IN_LOGS=True`.

---

## 6. Example `.env` Configuration File

Create a `.env` file in the project root to customize gateway behavior:

```env
# Core Application
APP_NAME="Secure Multi-Provider LLM Gateway"
APP_ENV="development"
DEBUG=false
HOST="127.0.0.1"
PORT=8000

# Client Authentication & Rate Limiting
GATEWAY_API_KEYS="gw-test-key-1,gw-test-key-2,gw-prod-custom-client"
RATE_LIMIT_PER_MINUTE=100

# Provider Endpoints
OLLAMA_BASE_URL="http://localhost:11434"
OLLAMA_DEFAULT_MODEL="qwen2.5:1.5b"
OLLAMA_FALLBACK_MODEL="gemma2:2b"
OLLAMA_REQUEST_TIMEOUT=60.0

# Cloud Groq Provider (Optional)
GROQ_API_KEY=""
GROQ_DEFAULT_MODEL="llama-3.1-8b-instant"

# Gateway Policy
DEFAULT_PROVIDER="mock"
ENABLE_STRUCTURED_LOGGING=true
LOG_LEVEL="INFO"
REDACT_SECRETS_IN_LOGS=true
```

---

## 7. Running the Gateway

Start the service using Uvicorn:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Interactive OpenAPI documentation is available at:
- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- System Health: `http://127.0.0.1:8000/api/v1/health`
