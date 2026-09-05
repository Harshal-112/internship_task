#!/usr/bin/env bash
# ==============================================================================
# Ollama Deployment & Verification Script for Linux / macOS
# Deploys Ollama, starts service, pulls qwen2.5:1.5b (fallback gemma2:2b),
# and verifies readiness via http://localhost:11434/api/tags.
# ==============================================================================

set -euo pipefail

MODEL="${1:-qwen2.5:1.5b}"
FALLBACK_MODEL="${2:-gemma2:2b}"
OLLAMA_URL="${3:-http://localhost:11434}"

echo "=========================================================="
echo "   Ollama Local LLM Deployment Script (Linux / macOS)   "
echo "=========================================================="
echo "Target Primary Model : ${MODEL}"
echo "Fallback Model       : ${FALLBACK_MODEL}"
echo "Ollama Service URL   : ${OLLAMA_URL}"
echo ""

# 1. Check if Ollama executable exists
echo "[1/4] Checking Ollama installation..."
if ! command -v ollama &> /dev/null; then
    echo "[!] Ollama is not installed. Installing via official installer..."
    if command -v curl &> /dev/null; then
        curl -fsSL https://ollama.com/install.sh | sh
    else
        echo "[x] curl is required to install Ollama automatically. Please install curl or Ollama manually."
        exit 1
    fi
fi

echo "[+] Ollama binary detected at: $(command -v ollama)"

# 2. Check if Ollama service is running
echo ""
echo "[2/4] Checking Ollama service connectivity at ${OLLAMA_URL}..."
is_service_running() {
    curl -s --connect-timeout 2 "${OLLAMA_URL}/api/tags" > /dev/null 2>&1
}

if ! is_service_running; then
    echo "[!] Ollama service is not running. Attempting to start in background..."
    if command -v systemctl &> /dev/null && systemctl is-active --quiet ollama; then
        echo "Restarting ollama via systemctl..."
        sudo systemctl start ollama || true
    else
        nohup ollama serve > /dev/null 2>&1 &
    fi

    echo "Waiting up to 15 seconds for service to initialize..."
    for i in {1..15}; do
        if is_service_running; then
            break
        fi
        sleep 1
    done
fi

if ! is_service_running; then
    echo "[x] Ollama service could not be reached at ${OLLAMA_URL}."
    echo "Please run 'ollama serve' in another terminal."
    exit 1
fi

echo "[+] Ollama service is running and responsive."

# 3. Pull primary model or fallback
echo ""
echo "[3/4] Pulling model: ${MODEL}..."
PULLED_MODEL=""

if ollama pull "${MODEL}"; then
    PULLED_MODEL="${MODEL}"
    echo "[+] Successfully pulled ${MODEL}"
else
    echo "[!] Failed to pull primary model (${MODEL}). Attempting fallback: ${FALLBACK_MODEL}..."
    if ollama pull "${FALLBACK_MODEL}"; then
        PULLED_MODEL="${FALLBACK_MODEL}"
        echo "[+] Successfully pulled fallback model ${FALLBACK_MODEL}"
    else
        echo "[x] Failed to pull fallback model (${FALLBACK_MODEL})."
        exit 1
    fi
fi

# 4. Verify model readiness via /api/tags
echo ""
echo "[4/4] Verifying model readiness via ${OLLAMA_URL}/api/tags..."
TAGS_RESPONSE=$(curl -s "${OLLAMA_URL}/api/tags")

if echo "${TAGS_RESPONSE}" | grep -q "${PULLED_MODEL}"; then
    echo ""
    echo "=========================================================="
    echo " [SUCCESS] Model '${PULLED_MODEL}' is ready for inference!"
    echo " Endpoint: ${OLLAMA_URL}"
    echo "=========================================================="
else
    echo "[!] Model '${PULLED_MODEL}' downloaded, but not found in /api/tags JSON output."
fi
