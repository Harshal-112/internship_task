"""Multi-Provider Router with intelligent dispatch and automatic failover."""

import asyncio
import logging
from typing import AsyncIterator, Dict, List, Optional

from app.core.config import get_settings
from app.providers.base import BaseLLMProvider, MockLLMProvider
from app.providers.groq_provider import GroqProvider
from app.providers.ollama_provider import OllamaProvider
from app.schemas.chat import ChatRequest, ChatResponse
from app.schemas.health import ProviderStatus

logger = logging.getLogger("gateway.router")


class LLMRouter:
    """Multi-provider LLM router managing Groq, Ollama, and Mock providers.

    Features:
    - Primary provider dispatch based on `request.provider` or configured default.
    - Automatic graceful failover to secondary providers when primary fails or is unconfigured.
    - Provider health monitoring (`get_providers_health`).
    - Stream routing with fallback capability.
    """

    def __init__(
        self,
        providers: Optional[Dict[str, BaseLLMProvider]] = None,
        default_provider: Optional[str] = None,
        failover_order: Optional[List[str]] = None,
    ):
        settings = get_settings()

        # Configured default provider name
        if default_provider:
            self._default_provider = default_provider
        elif hasattr(settings.DEFAULT_PROVIDER, "value"):
            self._default_provider = str(settings.DEFAULT_PROVIDER.value).lower()
        else:
            self._default_provider = str(settings.DEFAULT_PROVIDER).lower()

        # Initialize providers registry
        if providers is not None:
            self._providers = providers
        else:
            self._providers = {
                "mock": MockLLMProvider(model="mock-model", simulate_latency_ms=10.0, name="mock"),
                "ollama": OllamaProvider(fallback_to_mock=False),
                "groq": GroqProvider(fallback_to_mock=False),
            }

        # Priority order for fallback candidates
        self._failover_order = failover_order or ["groq", "ollama", "mock"]

    @property
    def providers(self) -> Dict[str, BaseLLMProvider]:
        """Dictionary of registered providers."""
        return self._providers

    @property
    def default_provider_name(self) -> str:
        """Name of the default provider."""
        return self._default_provider

    def get_provider(self, name: str) -> Optional[BaseLLMProvider]:
        """Retrieve a provider instance by name."""
        return self._providers.get(name.lower())

    def _build_candidate_chain(self, requested_provider: Optional[str]) -> List[str]:
        """Build the ordered list of provider candidates to attempt."""
        target = (requested_provider or self._default_provider).lower().strip()

        # If requested target is not registered, start with default provider or mock
        if target not in self._providers:
            logger.warning(
                f"Requested provider '{target}' is not registered. Falling back to default '{self._default_provider}'."
            )
            target = self._default_provider if self._default_provider in self._providers else "mock"

        # Build chain starting with target, then remaining in failover order, ending with mock
        chain: List[str] = [target]
        for name in self._failover_order:
            if name in self._providers and name not in chain:
                chain.append(name)

        # Ensure 'mock' is always the ultimate fallback if registered
        if "mock" in self._providers and "mock" not in chain:
            chain.append("mock")

        return chain

    async def route(self, request: ChatRequest) -> ChatResponse:
        """Route chat request to primary provider with automatic failover upon failure.

        Args:
            request: ChatRequest containing messages, model, and optional provider.

        Returns:
            ChatResponse from the first successful provider in the candidate chain.

        Raises:
            RuntimeError: If all candidate providers in the failover chain fail.
        """
        candidates = self._build_candidate_chain(request.provider)
        primary_provider = candidates[0]
        logger.info(f"Routing request to primary provider '{primary_provider}'. Candidate chain: {candidates}")

        errors: Dict[str, str] = {}

        for i, provider_name in enumerate(candidates):
            provider = self._providers[provider_name]
            is_fallback = (i > 0)

            if is_fallback:
                logger.warning(
                    f"Failing over to secondary provider '{provider_name}' (primary was '{primary_provider}')."
                )

            # Pre-check for Groq: if missing API key and we have fallbacks, skip directly
            if provider_name == "groq" and isinstance(provider, GroqProvider):
                if not provider.has_valid_key and len(candidates) > 1:
                    logger.warning("Groq API key not configured. Triggering automatic failover to next provider.")
                    errors["groq"] = "GROQ_API_KEY is not configured."
                    continue

            try:
                response = await provider.generate(request)
                if is_fallback:
                    logger.info(
                        f"Successfully handled request via fallback provider '{provider_name}' "
                        f"(model: {response.model})."
                    )
                return response
            except Exception as exc:
                error_desc = f"{type(exc).__name__}: {str(exc)}"
                errors[provider_name] = error_desc
                logger.warning(
                    f"Provider '{provider_name}' failed with {error_desc}. "
                    f"Remaining candidates: {candidates[i+1:]}"
                )
                continue

        # If all providers in chain failed
        all_errs = "; ".join(f"{k}: {v}" for k, v in errors.items())
        logger.error(f"All providers failed in router chain: {all_errs}")
        raise RuntimeError(f"All providers in failover chain failed: {all_errs}")

    async def stream_route(self, request: ChatRequest) -> AsyncIterator[str]:
        """Stream response chunks with automatic failover if primary provider fails initiation."""
        candidates = self._build_candidate_chain(request.provider)
        errors: Dict[str, str] = {}

        for i, provider_name in enumerate(candidates):
            provider = self._providers[provider_name]
            is_fallback = (i > 0)

            # Pre-check for Groq missing key
            if provider_name == "groq" and isinstance(provider, GroqProvider):
                if not provider.has_valid_key and len(candidates) > 1:
                    logger.warning("Groq API key not configured. Triggering stream failover.")
                    errors["groq"] = "GROQ_API_KEY is not configured."
                    continue

            try:
                stream_iter = provider.stream_generate(request)
                # Test the first chunk to ensure stream successfully starts
                first_chunk = await stream_iter.__anext__()
                yield first_chunk

                # Stream remaining chunks
                async for chunk in stream_iter:
                    yield chunk
                return
            except StopAsyncIteration:
                # Stream completed after first chunk or was empty
                return
            except Exception as exc:
                error_desc = f"{type(exc).__name__}: {str(exc)}"
                errors[provider_name] = error_desc
                logger.warning(
                    f"Stream initiation for provider '{provider_name}' failed with {error_desc}. "
                    f"Attempting failover..."
                )
                continue

        raise RuntimeError(f"All streaming providers failed: {errors}")

    async def get_providers_health(self) -> Dict[str, ProviderStatus]:
        """Check health and availability of all registered providers concurrently."""
        tasks = []
        names = list(self._providers.keys())

        for name in names:
            provider = self._providers[name]
            tasks.append(self._check_single_provider_health(name, provider))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        statuses: Dict[str, ProviderStatus] = {}
        for name, result in zip(names, results):
            if isinstance(result, ProviderStatus):
                statuses[name] = result
            elif isinstance(result, Exception):
                statuses[name] = ProviderStatus(
                    name=name,
                    available=False,
                    details=f"Health check error: {str(result)}",
                )
            else:
                statuses[name] = ProviderStatus(
                    name=name,
                    available=False,
                    details="Unknown health check status",
                )

        return statuses

    async def _check_single_provider_health(self, name: str, provider: BaseLLMProvider) -> ProviderStatus:
        """Check health of an individual provider with timeout and descriptive details."""
        try:
            available = await asyncio.wait_for(provider.check_health(), timeout=5.0)
            if available:
                details = f"Operational ({name} provider ready)"
            else:
                if name == "groq":
                    details = "GROQ_API_KEY not configured or cloud endpoint unreachable"
                elif name == "ollama":
                    details = "Ollama local service unreachable"
                else:
                    details = "Unavailable"
            return ProviderStatus(name=name, available=available, details=details)
        except Exception as exc:
            return ProviderStatus(
                name=name,
                available=False,
                details=f"Health check failed: {type(exc).__name__}",
            )


# Global router singleton
router_instance = LLMRouter()


def get_router() -> LLMRouter:
    """Retrieve global LLMRouter singleton."""
    return router_instance
