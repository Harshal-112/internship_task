"""Main FastAPI application entrypoint with structured logging & multi-provider routing."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import StructuredLoggingMiddleware, setup_logging

logger = logging.getLogger("gateway")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan context manager for startup and shutdown routines."""
    settings = get_settings()
    logger.info(f"Starting {settings.APP_NAME} in '{settings.APP_ENV}' mode...")
    logger.info(f"Loaded {len(settings.GATEWAY_API_KEYS)} client API keys. Rate limit: {settings.RATE_LIMIT_PER_MINUTE}/min.")
    yield
    logger.info(f"Shutting down {settings.APP_NAME}...")


def create_app() -> FastAPI:
    """Application factory for the FastAPI gateway service."""
    settings = get_settings()

    # Configure structured JSON logging
    setup_logging()

    app = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        description="Production-grade, secure, multi-provider LLM gateway with client auth & rate limiting.",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Middlewares (order: StructuredLoggingMiddleware wraps request, CORS handles pre-flight)
    app.add_middleware(StructuredLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Exception Handlers
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        """Standardized HTTP exception response handler preserving custom headers."""
        headers = dict(exc.headers or {})
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Standardized 422 Unprocessable Entity handler with detailed validation errors."""
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": exc.errors()},
        )

    # Mount API v1 router
    app.include_router(api_router, prefix="/api/v1")

    # Root service metadata endpoint
    @app.get(
        "/",
        summary="Service Metadata",
        tags=["Root"],
    )
    async def root_info():
        return {
            "name": settings.APP_NAME,
            "version": "1.0.0",
            "environment": settings.APP_ENV,
            "docs": "/docs",
            "health": "/api/v1/health",
        }

    return app


app = create_app()
