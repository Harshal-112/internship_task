# Subagent 1: Core Service Execution Log

**Module**: Core Service & Client Security (L1-06)  
**Evaluator Subagent**: `core-service` (Subagent 1)  
**Date**: 2026-09-05  
**Status**: Completed & Verified  

---

## 1. Features Built & Implemented

1. **Static Client Key Management & Masking (`app/services/key_manager.py`)**:
   - `KeyManager` service validating keys against configured gateway settings (`GATEWAY_API_KEYS`).
   - Secure key masking helper (`mask_key()`), transforming keys such as `gw-test-key-1` into `gw-test-key-****` to prevent secret leakage in log files.
   - Client identification utility for request state propagation and logging.

2. **Client Authentication & Authorization (`app/core/security.py`)**:
   - FastAPI dependency `verify_api_key` supporting both `X-API-Key` headers and standard `Authorization: Bearer <key>` headers.
   - Header case-insensitivity and fallback checks.
   - Enforces 401 Unauthorized status with `WWW-Authenticate: Bearer` on missing or invalid keys.
   - Populates `request.state.api_key`, `request.state.client_id`, and `request.state.masked_key`.

3. **In-Memory Sliding-Window Rate Limiter (`app/core/rate_limit.py`)**:
   - Thread-safe sliding-window rate limiter utilizing `asyncio.Lock` and monotonic timestamps.
   - Tracks quota per authenticated client API key with fallback to client host IP.
   - Configurable per-minute window with automated pruning of expired records.
   - Enforces 429 Too Many Requests and calculates dynamic `Retry-After` header indicating seconds until the oldest request in the window expires.
   - Factory functions and test utility methods (`reset()`, `set_limit()`).

4. **Health Check Endpoint (`app/api/v1/endpoints/health.py`)**:
   - `GET /api/v1/health` responding with OpenAI/gateway `HealthResponse` schema.
   - Reports service health status (`healthy`/`degraded`), version, timestamp, and granular provider availability (`mock`, `ollama`, `groq`).

5. **Chat Completion Endpoint (`app/api/v1/endpoints/chat.py`)**:
   - `POST /api/v1/chat` adhering to `ChatRequest` schema.
   - Enforces `verify_api_key` and `rate_limiter` dependencies.
   - Dispatches requests to `MockLLMProvider` (Layer 1 baseline), returning `ChatResponse` with simulated token usage and execution latency.
   - Supports text/event streaming when `stream=True` via `StreamingResponse`.

6. **API Routing & App Factory (`app/api/v1/router.py`, `app/main.py`)**:
   - Clean modular routing mounted under `/api/v1`.
   - Lifespan context manager logging service startup and shutdown.
   - CORS middleware enabled for cross-origin client integration.
   - Standardized exception handlers for 401 (Authentication), 429 (Rate Limit with `Retry-After`), and 422 (Pydantic validation errors).
   - Root `/` endpoint exposing service metadata and link to `/docs`.

7. **Comprehensive Pytest Suite (`tests/test_core_service.py`)**:
   - 16 distinct unit and integration test cases using `fastapi.testclient.TestClient`.
   - Full coverage of health status, authentication schemes, rate limit throttling, client key isolation, validation errors (422), and key masking.

8. **Documentation (`docs/configuration.md`)**:
   - Comprehensive reference of all environment variables, client API key setup instructions, rate limiting semantics, and security best practices.

---

## 2. Key Architectural Decisions

- **FastAPI Dependency Injection for Security & Rate Limiting**:
  - Implemented `verify_api_key` and `rate_limiter` as composable FastAPI dependencies (`Depends()`).
  - This allows future endpoints (e.g. models listing, token counting) to easily attach auth and rate limiting without code duplication.
- **Dual Authentication Scheme**:
  - Supported both `X-API-Key` and `Authorization: Bearer <key>` to ensure compatibility with standard HTTP clients, curl, and OpenAI-compatible client SDKs.
- **In-Memory Sliding Window over Fixed Window**:
  - Selected a sliding-window log approach over fixed-window to eliminate the "boundary burst" vulnerability (where a client exhausts 2x the rate limit at window boundary resets).
  - Dynamically calculates exact `Retry-After` response headers, giving clients deterministic backoff times.
- **Clean Separation of Concerns**:
  - `KeyManager` handles key validation and sanitization.
  - `verify_api_key` handles HTTP transport extraction and authorization checks.
  - `rate_limiter` handles quota tracking and throttling.
  - Schemas and providers remain untouched and clean.

---

## 3. Trade-offs Made

- **Static Key List vs. Database / Vault Key Storage**:
  - Per project instructions for Layer 1, static keys via `GATEWAY_API_KEYS` were chosen. This satisfies the simplicity rule, eliminates database dependencies, and enables high-performance in-memory lookups ($O(1)$ set lookup).
- **In-Memory Rate Limiting vs. Distributed Redis Limiter**:
  - In-memory rate limiting was chosen for standalone zero-dependency simplicity and fast offline execution.
  - In multi-instance deployments behind a reverse proxy, a Redis-backed token bucket or sticky load balancing could be introduced if shared state is required.
- **Mock Provider Integration**:
  - For Layer 1 base service, `/chat` dispatches to `MockLLMProvider`, providing immediate testability and deterministic assertions for token counting and latency before Layer 2 (Ollama) and Layer 3 (Groq) providers are plugged in.

---

## 4. Test Results

Command executed:
```bash
pytest tests/test_core_service.py -v
```

Execution output:
```
============================= test session starts =============================
platform win32 -- Python 3.13.9, pytest-9.1.1, pluggy-1.6.0 -- D:\internship_task\.venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: D:\internship_task
plugins: anyio-4.15.0, asyncio-1.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 16 items

tests/test_core_service.py::test_health_check_returns_200_and_healthy PASSED [  6%]
tests/test_core_service.py::test_root_endpoint_metadata PASSED           [ 12%]
tests/test_core_service.py::test_chat_with_valid_x_api_key_returns_200 PASSED [ 18%]
tests/test_core_service.py::test_chat_with_valid_bearer_token_returns_200 PASSED [ 25%]
tests/test_core_service.py::test_chat_with_missing_api_key_returns_401 PASSED [ 31%]
tests/test_core_service.py::test_chat_with_invalid_api_key_returns_401 PASSED [ 37%]
tests/test_core_service.py::test_chat_with_invalid_bearer_token_returns_401 PASSED [ 43%]
tests/test_core_service.py::test_rate_limiting_triggers_429_when_threshold_exceeded PASSED [ 50%]
tests/test_core_service.py::test_rate_limiting_isolated_per_client_key PASSED [ 56%]
tests/test_core_service.py::test_invalid_schema_missing_messages_triggers_422 PASSED [ 62%]
tests/test_core_service.py::test_invalid_schema_empty_messages_list_triggers_422 PASSED [ 68%]
tests/test_core_service.py::test_invalid_schema_empty_message_content_triggers_422 PASSED [ 75%]
tests/test_core_service.py::test_invalid_schema_unsupported_role_triggers_422 PASSED [ 81%]
tests/test_core_service.py::test_chat_streaming_response PASSED          [ 87%]
tests/test_core_service.py::test_key_manager_validation PASSED           [ 93%]
tests/test_core_service.py::test_key_manager_masking PASSED              [100%]

======================= 16 passed, 2 warnings in 0.76s ========================
```

All 16 tests executed and passed cleanly.
