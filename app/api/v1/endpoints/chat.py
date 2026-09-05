"""Chat completions endpoint with multi-provider routing and telemetry."""

from typing import Union
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse

from app.core.rate_limit import rate_limiter
from app.core.security import verify_api_key
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.router import get_router

router = APIRouter()
router_service = get_router()


@router.post(
    "",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Create Chat Completion",
    description="Generate a chat completion response using configured provider. Requires client authentication."
)
@router.post(
    "/",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False
)
async def create_chat_completion(
    request: ChatRequest,
    raw_request: Request,
    client_id: str = Depends(verify_api_key),
    _: None = Depends(rate_limiter),
) -> Union[ChatResponse, StreamingResponse]:
    """Execute chat completion with multi-provider routing and automatic failover.

    Validates request payload, enforces API key authentication and rate limiting.
    Routes to Groq, Ollama, or Mock provider with automatic failover.
    Records telemetry in request state for structured logging.
    """
    # Attach initial routing target to request state for observability
    raw_request.state.provider = request.provider or router_service.default_provider_name
    raw_request.state.model = request.model or "default"

    # If streaming is requested, return SSE / text stream
    if request.stream:
        return StreamingResponse(
            router_service.stream_route(request),
            media_type="text/event-stream",
        )

    # Standard completion response via router with failover
    response = await router_service.route(request)

    # Update request state with actual provider/model used and usage stats for logging
    raw_request.state.provider = response.provider
    raw_request.state.model = response.model
    raw_request.state.usage = response.usage

    return response
