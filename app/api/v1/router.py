"""API v1 root router combining all v1 endpoints."""

from fastapi import APIRouter
from app.api.v1.endpoints import chat, health

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["Health"])
api_router.include_router(chat.router, prefix="/chat", tags=["Chat"])
