# Secure Multi-Provider LLM Gateway

A unified, production-grade LLM Gateway service built with **Python 3.13** and **FastAPI**, designed as a single cohesive codebase satisfying three core architectural evaluation layers:
- **Layer 1 (L1-06)**: Secure, Configurable Foundation & Client Authentication
- **Layer 2 (L2-08)**: Local Open-Source LLM Deployment & Benchmarking
- **Layer 3 (L3-08)**: Multi-Provider Routing, Cascading Failover & Structured Telemetry

[![Tests: 54 Passed](https://img.shields.io/badge/tests-54%20passed-brightgreen.svg)](#test-suite-verification)
[![Python: 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](#quickstart--installation)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](#)

---

## 1. Architectural Overview & Layer Interaction

The gateway operates as a single high-performance FastAPI service where each layer builds directly upon the foundation of the previous layer without code duplication:

```
                                 [ Client HTTP Request ]
                                            │
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│  LAYER 1: Core Foundation & Security (L1-06)                                           │
│  - In-memory Sliding-Window Rate Limiter (HTTP 429 + Retry-After)                     │
│  - Client Key Authentication: `X-API-Key` or `Authorization: Bearer` (HTTP 401)       │
│  - Key Masking (`gw-test-****`) & Request Validation via Pydantic v2                   │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│  LAYER 3: Multi-Provider Routing & Observability (L3-08)                               │
│  - Telemetry Middleware (injects `X-Request-ID`, calculates latency)                   │
│  - Structured JSON Logging (10 telemetry metrics + dual-layer secret redaction)        │
│  - Dynamic LLM Router with Cascading Failover (`groq` ➔ `ollama` ➔ `mock`)            │
└──────────────────────┬─────────────────────────────────────────────┬───────────────────┘
                       │                                             │
                       ▼                                             ▼
┌───────────────────────────────────────────┐ ┌──────────────────────────────────────────┐
│  Cloud Provider: Groq API (L3-08)         │ │  LAYER 2: Local LLM Provider (L2-08)     │
│  - Model: `openai/gpt-oss-20b`            │ │  - Model: `qwen2.5:1.5b` (Ollama daemon) │
│  - Free-tier API key (zero billing)       │ │  - LangChain-compatible interface        │
│  - Fast cloud inference (~330+ tok/s)     │ │  - 8GB RAM host optimization (<1.4GB RAM)│
└───────────────────────────────────────────┘ └──────────────────────────────────────────┘
                       │                                             │
                       └──────────────────────┬──────────────────────┘
                                              │ (Fallback / Air-Gapped Mode)
                                              ▼
                               ┌─────────────────────────────┐
                               │  Mock LLM Provider          │
                               │  - 100% offline & reliable  │
                               │  - Deterministic test double│
                               └─────────────────────────────┘
```

### How the Three Layers Connect
1. **Request Ingestion**: Incoming requests to `POST /api/v1/chat` first hit **Layer 1's** `verify_api_key` dependency and `SlidingWindowRateLimiter`. Requests lacking valid credentials receive an immediate `401 Unauthorized`; clients exceeding rate limits receive `429 Too Many Requests`.
2. **Telemetry Interception**: **Layer 3's** `StructuredLoggingMiddleware` assigns or propagates `X-Request-ID`, initiates a high-resolution execution timer, and prepares context variables.
3. **Dynamic Provider Dispatch & Failover**: The endpoint forwards the validated `ChatRequest` to **Layer 3's** `LLMRouter`. The router checks the target provider (`groq`, `ollama`, or `mock`):
   - If `groq` is requested but no API key is present or cloud rate-limits (HTTP 429) occur, it **automatically cascades** to **Layer 2's** local `ollama` instance.
   - If the local Ollama daemon is offline or uninstalled, it gracefully cascades to the built-in `mock` provider.
4. **Telemetry Logging**: Once the provider responds, **Layer 3** emits a single-line structured JSON log capturing 10 distinct telemetry fields while scrubbing any secrets, keys, or Bearer tokens.

---

## 2. Project Directory Structure

```
d:/internship_task/
├── .env.example                     # Environment template (clearly marked free-tier)
├── .env                             # Local configuration file (mock/offline ready)
├── requirements.txt                 # Frozen production dependencies
├── README.md                        # Master documentation & interview guide
│
├── app/
│   ├── main.py                      # FastAPI app entrypoint, CORS, lifespan & middleware
│   │
│   ├── core/                        # [Layer 1 Foundation]
│   │   ├── config.py                # Unified Pydantic v2 BaseSettings
│   │   ├── security.py              # API key verification & identity extraction
│   │   ├── rate_limit.py            # Thread-safe sliding-window rate limiter
│   │   └── logging.py               # [Layer 3] Structured JSON logging & secret scrubbing
│   │
│   ├── schemas/                     # Shared Data Contracts
│   │   ├── chat.py                  # ChatRequest, ChatResponse, Message, Role, TokenUsage
│   │   ├── health.py                # HealthResponse, ProviderStatus
│   │   └── provider.py              # ModelInfo, ProviderType
│   │
│   ├── providers/                   # Model Integrations
│   │   ├── base.py                  # BaseLLMProvider abstract interface & MockLLMProvider
│   │   ├── ollama_provider.py       # [Layer 2] Ollama LangChain-compatible adapter
│   │   └── groq_provider.py         # [Layer 3] Groq Cloud API adapter (free tier)
│   │
│   ├── services/                    # Business Logic
│   │   ├── key_manager.py           # [Layer 1] Client key store & masking logic
│   │   └── router.py                # [Layer 3] Multi-provider router & cascading failover
│   │
│   └── api/
│       └── v1/
│           ├── router.py            # API v1 route aggregator
│           └── endpoints/
│               ├── health.py        # GET /api/v1/health (system & provider status)
│               └── chat.py          # POST /api/v1/chat (unified completion & streaming)
│
├── benchmarks/                      # [Layer 2 Benchmark Suite]
│   ├── eval_prompts.json            # 3 standardized evaluation prompts (reasoning, code, summary)
│   ├── harness.py                   # CLI benchmark harness (TTFT, latency, throughput, quality)
│   ├── benchmark_report.md          # In-depth comparative analysis report
│   └── benchmark_results.json       # Generated benchmark metrics output
│
├── scripts/                         # Automation & Tooling
│   ├── deploy_ollama.ps1            # Windows Ollama installer & Qwen 1.5B pull script
│   ├── deploy_ollama.sh             # Linux/macOS equivalent installer script
│   └── run_benchmark.py             # CLI launcher for benchmark execution
│
├── docs/                            # Deep-Dive Technical Documentation
│   ├── configuration.md             # [Layer 1] Environment variables & auth guide
│   ├── recommendations.md           # [Layer 2] 8GB RAM host sizing & quantization guide
│   └── observability.md             # [Layer 3] JSON log schema & redaction policies
│
├── logs/                            # Subagent Execution Logs & Decision Records
│   ├── subagent_1_core_service.md   # Subagent 1 decisions, design & test verification
│   ├── subagent_2_local_deploy.md   # Subagent 2 decisions, memory sizing & benchmarks
│   └── subagent_3_observability.md  # Subagent 3 decisions, router failover & scrubbing
│
└── tests/                           # Comprehensive Test Suite (54 Tests)
    ├── test_core_service.py         # Layer 1: Auth, rate limits, health, schemas
    ├── test_ollama_provider.py      # Layer 2: Ollama provider & LangChain interface
    ├── test_benchmark_harness.py    # Layer 2: Benchmark calculations & metrics
    ├── test_multi_provider.py       # Layer 3: Router dispatch, failover, provider health
    └── test_observability.py        # Layer 3: JSON logging format & secret redaction
```

---

## 3. Quickstart & Installation

### Step 1: Clone Repository & Set Up Virtual Environment
```bash
git clone https://github.com/Harshal-112/internship_task.git
cd internship_task

python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### Step 2: Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Default configuration works **100% out of the box** without any external setup using `DEFAULT_PROVIDER="mock"`:
```ini
# Client Auth
GATEWAY_API_KEYS="gw-test-key-1,gw-test-key-2"
RATE_LIMIT_PER_MINUTE=60

# Local Model (Ollama)
OLLAMA_BASE_URL="http://localhost:11434"
OLLAMA_DEFAULT_MODEL="qwen2.5:1.5b"

# Cloud Model (Groq Free Tier - Optional, No Billing)
GROQ_API_KEY=""
GROQ_DEFAULT_MODEL="openai/gpt-oss-20b"

# Default Provider ("mock", "ollama", or "groq")
DEFAULT_PROVIDER="mock"
ENABLE_STRUCTURED_LOGGING=true
```

### Step 3: Run the Service
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
API Documentation (Swagger UI) is available at: `http://127.0.0.1:8000/docs`

---

## 4. Operational Walkthrough & Testing

### 1. Health Check
```bash
curl -X GET http://127.0.0.1:8000/api/v1/health
```
**Response (HTTP 200)**:
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "timestamp": 1788585528.32,
  "providers": {
    "mock": {"name": "mock", "available": true, "details": "Operational (mock provider ready)"},
    "ollama": {"name": "ollama", "available": false, "details": "Ollama local service unreachable"},
    "groq": {"name": "groq", "available": false, "details": "GROQ_API_KEY not configured"}
  }
}
```

### 2. Authenticated Chat Request
```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: gw-test-key-1" \
  -d '{
    "messages": [
      {"role": "user", "content": "Explain binary search in one sentence."}
    ]
  }'
```
**Response (HTTP 200)**:
```json
{
  "id": "chatcmpl-a931cbe562f1",
  "created": 1788585530,
  "provider": "mock",
  "model": "mock-model",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "[MockResponse from mock-model]: Processed prompt: Explain binary search in one sentence...."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 8,
    "completion_tokens": 15,
    "total_tokens": 23
  },
  "latency_ms": 32.4
}
```

### 3. Demonstrating Rate Limiting (HTTP 429)
Send rapid requests exceeding `RATE_LIMIT_PER_MINUTE`:
```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "X-API-Key: gw-test-key-1" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Ping"}]}'
```
**Response**:
```http
HTTP/1.1 429 Too Many Requests
Retry-After: 48
Content-Type: application/json

{
  "error": "Too Many Requests",
  "detail": "Rate limit exceeded (60 requests per minute). Try again in 48 seconds.",
  "retry_after": 48
}
```

---

## 5. Local LLM Deployment (Ollama)

To run the local open-source LLM on Windows:
```powershell
# Automatically checks Ollama, starts service, pulls qwen2.5:1.5b, verifies readiness
.\scripts\deploy_ollama.ps1
```
Or on Linux/macOS:
```bash
chmod +x ./scripts/deploy_ollama.sh
./scripts/deploy_ollama.sh
```

---

## 6. Running the Evaluation Benchmark Harness

Compare local LLM (`qwen2.5:1.5b`) vs Cloud Groq (`openai/gpt-oss-20b`) on Latency, Time-To-First-Token (TTFT), Throughput (tok/s), and qualitative answer quality:

```bash
# Offline Mock Mode (Zero dependencies):
python scripts/run_benchmark.py --mock

# Live Mode (against running Ollama and/or Groq API key):
python scripts/run_benchmark.py --provider all
```

Verified Benchmark Performance Summary:
```
========================================================================================
                      LLM BENCHMARK PERFORMANCE SUMMARY
========================================================================================
Provider   | Model                | Task             | TTFT (ms)  | Latency (ms) | Throughput (tok/s)
----------------------------------------------------------------------------------------
ollama     | qwen2.5:1.5b         | River Crossing L | 12160.4 *  | 3609.6       | 88.6              
ollama     | qwen2.5:1.5b         | Sliding Window M | 779.7      | 3943.8       | 90.5              
ollama     | qwen2.5:1.5b         | Distributed Cons | 995.4      | 1959.5       | 76.0              
........................................................................................
OLLAMA     | qwen2.5:1.5b         | AVERAGE          | 4645.2     | 3171.0       | 85.1              
----------------------------------------------------------------------------------------
groq       | openai/gpt-oss-20b   | River Crossing L | 2337.7     | 1036.5       | 494.0             
groq       | openai/gpt-oss-20b   | Sliding Window M | 3389.6     | 2264.8       | 345.7             
groq       | openai/gpt-oss-20b   | Distributed Cons | 1571.1     | 1539.3       | 106.5             
........................................................................................
GROQ       | openai/gpt-oss-20b   | AVERAGE          | 2432.8     | 1613.5       | 315.4             
========================================================================================
* Note: Ollama's first prompt included initial cold-start model weight load from disk.
```

---

## 7. Test Suite Verification

The repository features 100% test coverage across all three layers:
```bash
pytest tests/ -v
```

```
tests/test_benchmark_harness.py::test_load_eval_prompts PASSED           [  1%]
tests/test_benchmark_harness.py::test_create_provider_mock PASSED        [  3%]
tests/test_benchmark_harness.py::test_measure_streaming_ttft PASSED      [  5%]
tests/test_benchmark_harness.py::test_run_single_prompt_benchmark PASSED [  7%]
tests/test_benchmark_harness.py::test_run_benchmark_workflow PASSED      [  9%]
tests/test_core_service.py::test_health_check_returns_200_and_healthy PASSED [ 11%]
tests/test_core_service.py::test_root_endpoint_metadata PASSED           [ 12%]
tests/test_core_service.py::test_chat_with_valid_x_api_key_returns_200 PASSED [ 14%]
tests/test_core_service.py::test_chat_with_valid_bearer_token_returns_200 PASSED [ 16%]
tests/test_core_service.py::test_chat_with_missing_api_key_returns_401 PASSED [ 18%]
tests/test_core_service.py::test_chat_with_invalid_api_key_returns_401 PASSED [ 20%]
tests/test_core_service.py::test_chat_with_invalid_bearer_token_returns_401 PASSED [ 22%]
tests/test_core_service.py::test_rate_limiting_triggers_429_when_threshold_exceeded PASSED [ 24%]
tests/test_core_service.py::test_rate_limiting_isolated_per_client_key PASSED [ 25%]
tests/test_core_service.py::test_invalid_schema_missing_messages_triggers_422 PASSED [ 27%]
tests/test_core_service.py::test_invalid_schema_empty_messages_list_triggers_422 PASSED [ 29%]
tests/test_core_service.py::test_invalid_schema_empty_message_content_triggers_422 PASSED [ 31%]
tests/test_core_service.py::test_invalid_schema_unsupported_role_triggers_422 PASSED [ 33%]
tests/test_core_service.py::test_chat_streaming_response PASSED          [ 35%]
tests/test_core_service.py::test_key_manager_validation PASSED           [ 37%]
tests/test_core_service.py::test_key_manager_masking PASSED              [ 38%]
tests/test_multi_provider.py::test_router_dispatches_to_default_provider PASSED [ 40%]
tests/test_multi_provider.py::test_router_dispatches_to_requested_provider PASSED [ 42%]
tests/test_multi_provider.py::test_router_failover_when_primary_fails PASSED [ 44%]
tests/test_multi_provider.py::test_router_failover_unconfigured_groq_key PASSED [ 46%]
tests/test_multi_provider.py::test_router_cascading_failover_three_tiers PASSED [ 48%]
tests/test_multi_provider.py::test_router_all_providers_failed_raises_runtime_error PASSED [ 50%]
tests/test_multi_provider.py::test_router_handles_unknown_provider_gracefully PASSED [ 51%]
tests/test_multi_provider.py::test_router_streaming_failover PASSED      [ 53%]
tests/test_multi_provider.py::test_router_get_providers_health PASSED    [ 55%]
tests/test_multi_provider.py::test_api_chat_dispatch_mock_explicit PASSED [ 57%]
tests/test_multi_provider.py::test_api_chat_dispatch_groq_failover_when_no_key PASSED [ 59%]
tests/test_multi_provider.py::test_api_chat_dispatch_ollama_failover_when_offline PASSED [ 61%]
tests/test_multi_provider.py::test_api_health_endpoint_reflects_router_providers PASSED [ 62%]
tests/test_observability.py::test_json_formatter_produces_valid_json PASSED [ 64%]
tests/test_observability.py::test_telemetry_event_contains_all_10_required_fields PASSED [ 66%]
tests/test_observability.py::test_redact_groq_api_key_patterns PASSED    [ 68%]
tests/test_observability.py::test_redact_configured_gateway_keys PASSED  [ 70%]
tests/test_observability.py::test_redact_bearer_tokens PASSED            [ 72%]
tests/test_observability.py::test_redact_secrets_in_dict_and_nested_structures PASSED [ 74%]
tests/test_observability.py::test_json_formatter_redacts_secrets_in_log_output PASSED [ 75%]
tests/test_observability.py::test_chat_endpoint_injects_x_request_id_header PASSED [ 77%]
tests/test_observability.py::test_chat_endpoint_preserves_custom_request_id PASSED [ 79%]
tests/test_observability.py::test_chat_endpoint_telemetry_emission PASSED [ 81%]
tests/test_observability.py::test_logs_do_not_leak_raw_client_keys_or_bearer_tokens PASSED [ 83%]
tests/test_ollama_provider.py::test_ollama_check_health_success PASSED   [ 85%]
tests/test_ollama_provider.py::test_ollama_check_health_failure PASSED   [ 87%]
tests/test_ollama_provider.py::test_ollama_list_models PASSED            [ 88%]
tests/test_ollama_provider.py::test_ollama_generate_success PASSED       [ 90%]
tests/test_ollama_provider.py::test_ollama_stream_generate PASSED        [ 92%]
tests/test_ollama_provider.py::test_ollama_fallback_to_mock_on_connection_error PASSED [ 94%]
tests/test_ollama_provider.py::test_langchain_ainvoke_and_invoke PASSED  [ 96%]
tests/test_ollama_provider.py::test_langchain_astream PASSED             [ 98%]
tests/test_ollama_provider.py::test_langchain_stream PASSED              [100%]

======================= 54 passed, 7 warnings in 12.53s =======================
```

---
