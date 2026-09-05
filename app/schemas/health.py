"""Health check response schemas."""

import time
from typing import Dict, Optional
from pydantic import BaseModel, Field


class ProviderStatus(BaseModel):
    name: str
    available: bool
    details: Optional[str] = None


class HealthResponse(BaseModel):
    status: str = Field(default="healthy", description="'healthy' or 'degraded'")
    version: str = "1.0.0"
    timestamp: float = Field(default_factory=lambda: time.time())
    providers: Dict[str, ProviderStatus] = Field(default_factory=dict)
