"""Health check endpoint with dynamic multi-provider health monitoring."""

import time
from fastapi import APIRouter
from app.schemas.health import HealthResponse
from app.services.router import get_router

router = APIRouter()
router_service = get_router()


@router.get(
    "",
    response_model=HealthResponse,
    summary="System Health and Provider Status",
    description="Returns gateway status, version, timestamp, and provider availability."
)
@router.get(
    "/",
    response_model=HealthResponse,
    include_in_schema=False
)
async def get_health() -> HealthResponse:
    """Retrieve system health and individual provider statuses."""
    providers = await router_service.get_providers_health()

    # Gateway is healthy if at least one provider is available (e.g. Mock or active LLM)
    is_healthy = any(p.available for p in providers.values())
    overall_status = "healthy" if is_healthy else "degraded"

    return HealthResponse(
        status=overall_status,
        version="1.0.0",
        timestamp=time.time(),
        providers=providers,
    )
