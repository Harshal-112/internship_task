"""Rate limiting dependency and middleware for LLM Gateway."""

import asyncio
import math
import time
from typing import Dict, List, Optional
from fastapi import HTTPException, Request, status
from app.core.config import Settings, get_settings


class SlidingWindowRateLimiter:
    """In-memory sliding-window rate limiter.

    Tracks request timestamps within a sliding window (default: 60 seconds).
    Identifies clients by API key (from request state/headers) or client IP.
    Returns HTTP 429 Too Many Requests with a calculated 'Retry-After' header.
    """

    def __init__(
        self,
        rate_limit_per_minute: Optional[int] = None,
        window_seconds: int = 60,
    ):
        self._rate_limit = rate_limit_per_minute
        self.window_seconds = window_seconds
        self._history: Dict[str, List[float]] = {}
        self._lock = asyncio.Lock()

    def get_limit(self) -> int:
        """Get active rate limit count for the window."""
        if self._rate_limit is not None:
            return self._rate_limit
        return get_settings().RATE_LIMIT_PER_MINUTE

    def set_limit(self, limit: int) -> None:
        """Set or override rate limit (useful for testing or dynamic tiering)."""
        self._rate_limit = limit

    def reset(self) -> None:
        """Clear all tracked request history."""
        self._history.clear()

    def clear(self) -> None:
        """Alias for reset."""
        self.reset()

    @staticmethod
    def extract_client_identifier(request: Request) -> str:
        """Extract a unique client identifier for rate-limiting.

        Priority:
        1. Authenticated api_key from request.state (set by verify_api_key)
        2. 'X-API-Key' header
        3. 'Authorization: Bearer <key>' header
        4. Client IP address
        """
        # 1. State from verify_api_key
        if hasattr(request.state, "api_key") and request.state.api_key:
            return f"key:{request.state.api_key}"

        # 2. X-API-Key header
        x_key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
        if x_key:
            return f"key:{x_key.strip()}"

        # 3. Authorization Bearer
        auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth_header and auth_header.strip().lower().startswith("bearer "):
            parts = auth_header.strip().split()
            if len(parts) >= 2:
                return f"key:{parts[1].strip()}"

        # 4. Fallback to client IP
        if request.client and request.client.host:
            return f"ip:{request.client.host}"

        return "ip:127.0.0.1"

    async def check_rate_limit(self, request: Request) -> None:
        """Evaluate whether the request is within rate limits.

        Records request timestamp if allowed, or raises HTTP 429 with Retry-After header.
        """
        client_id = self.extract_client_identifier(request)
        limit = self.get_limit()
        now = time.monotonic()
        cutoff = now - self.window_seconds

        async with self._lock:
            timestamps = self._history.get(client_id, [])
            # Discard timestamps outside the sliding window
            valid_timestamps = [t for t in timestamps if t > cutoff]

            if len(valid_timestamps) >= limit:
                oldest = valid_timestamps[0]
                retry_after = max(1, math.ceil(self.window_seconds - (now - oldest)))
                self._history[client_id] = valid_timestamps
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded: maximum {limit} requests per minute allowed.",
                    headers={"Retry-After": str(retry_after)},
                )

            # Record this valid request
            valid_timestamps.append(now)
            self._history[client_id] = valid_timestamps

    async def __call__(self, request: Request) -> None:
        """Allows rate limiter instance to be used directly as a FastAPI dependency."""
        await self.check_rate_limit(request)


# Singleton rate limiter instance
rate_limiter = SlidingWindowRateLimiter()


def create_rate_limiter(limit: int, window_seconds: int = 60) -> SlidingWindowRateLimiter:
    """Factory helper to create custom-scoped rate limiters."""
    return SlidingWindowRateLimiter(rate_limit_per_minute=limit, window_seconds=window_seconds)
