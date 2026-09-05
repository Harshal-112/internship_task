"""Unit tests for Benchmark Harness and metric calculations."""

import asyncio
from pathlib import Path
import pytest

from benchmarks.harness import (
    PromptResult,
    ProviderSummary,
    create_provider,
    load_eval_prompts,
    measure_streaming_ttft,
    run_benchmark,
    run_single_prompt_benchmark,
)
from app.schemas.chat import ChatRequest, Message, Role
from app.providers.base import MockLLMProvider


@pytest.fixture
def eval_prompts_file(tmp_path):
    prompts_json = tmp_path / "test_prompts.json"
    prompts_json.write_text("""[
        {
            "id": "test-01",
            "category": "reasoning",
            "title": "Test Reasoning",
            "description": "Test description",
            "system_prompt": "You are a test system.",
            "prompt": "Solve 1 + 1.",
            "evaluation_criteria": ["Solves 1+1"]
        }
    ]""", encoding="utf-8")
    return prompts_json


def test_load_eval_prompts(eval_prompts_file):
    prompts = load_eval_prompts(eval_prompts_file)
    assert len(prompts) == 1
    assert prompts[0]["id"] == "test-01"
    assert prompts[0]["category"] == "reasoning"


def test_create_provider_mock():
    prov = create_provider("mock", mock_mode=True)
    assert isinstance(prov, MockLLMProvider)
    assert prov.name == "mock"

    ollama_mock = create_provider("ollama", mock_mode=True)
    assert ollama_mock.name == "ollama"
    assert ollama_mock.default_model == "qwen2.5:1.5b"


@pytest.mark.asyncio
async def test_measure_streaming_ttft():
    provider = MockLLMProvider(model="mock-model", simulate_latency_ms=10.0)
    req = ChatRequest(
        messages=[Message(role=Role.USER, content="Hello test")]
    )
    ttft_ms, total_lat_ms, tok_count, text = await measure_streaming_ttft(provider, req)

    assert ttft_ms >= 0.0
    assert total_lat_ms >= ttft_ms
    assert tok_count > 0
    assert len(text) > 0


@pytest.mark.asyncio
async def test_run_single_prompt_benchmark():
    provider = MockLLMProvider(model="test-model", simulate_latency_ms=15.0, name="mock")
    prompt_data = {
        "id": "p-01",
        "category": "logic",
        "title": "Logic Test",
        "prompt": "Evaluate True and False"
    }

    result = await run_single_prompt_benchmark(provider, prompt_data, runs=2)

    assert isinstance(result, PromptResult)
    assert result.prompt_id == "p-01"
    assert result.success is True
    assert result.ttft_ms > 0
    assert result.total_latency_ms > 0
    assert result.throughput_tok_per_sec > 0
    assert len(result.output_text) > 0


@pytest.mark.asyncio
async def test_run_benchmark_workflow(eval_prompts_file):
    prompts = load_eval_prompts(eval_prompts_file)
    prov1 = MockLLMProvider(model="model-a", simulate_latency_ms=10.0, name="prov-a")
    prov2 = MockLLMProvider(model="model-b", simulate_latency_ms=10.0, name="prov-b")

    summaries = await run_benchmark([prov1, prov2], prompts, runs=1)

    assert "prov-a" in summaries
    assert "prov-b" in summaries

    summary_a = summaries["prov-a"]
    assert isinstance(summary_a, ProviderSummary)
    assert summary_a.avg_ttft_ms > 0
    assert summary_a.avg_latency_ms > 0
    assert len(summary_a.results) == 1
    assert summary_a.results[0].prompt_id == "test-01"
