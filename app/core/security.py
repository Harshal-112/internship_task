"""Authentication and security dependencies for API Gateway."""

from typing import Optional
from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials
from app.core.config import get_settings
from app.services.key_manager import key_manager

# Security schemes for OpenAPI documentation
api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False, description="Client API Key header")
http_bearer_scheme = HTTPBearer(auto_error=False, description="Client API Key passed as Bearer token")


async def verify_api_key(
    request: Request,
    api_key_header: Optional[str] = Security(api_key_header_scheme),
    bearer_credentials: Optional[HTTPAuthorizationCredentials] = Security(http_bearer_scheme),
) -> str:
    """Verify incoming client API key against configured gateway keys.

    Accepts the API key from:
    1. 'X-API-Key' request header
    2. 'Authorization: Bearer <key>' request header

    Raises:
        HTTPException: 401 Unauthorized if key is missing or not authorized.

    Returns:
        str: Validated client key / identifier for downstream rate-limiting and logging.
    """
    settings = get_settings()
    raw_key: Optional[str] = None

    # 1. Check X-API-Key header scheme
    if api_key_header:
        raw_key = api_key_header.strip()

    # 2. Check Bearer token scheme
    elif bearer_credentials and bearer_credentials.credentials:
        raw_key = bearer_credentials.credentials.strip()

    # 3. Direct header fallback (case-insensitive)
    if not raw_key:
        x_key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
        if x_key:
            raw_key = x_key.strip()
        else:
            auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
            if auth_header and auth_header.strip().lower().startswith("bearer "):
                parts = auth_header.strip().split()
                if len(parts) >= 2:
                    raw_key = parts[1].strip()

    # Validate against configured gateway keys
    if not raw_key or not settings.is_valid_gateway_key(raw_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Provide a valid key via 'X-API-Key' or 'Authorization: Bearer <key>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Attach authentication context to request state
    masked_key = key_manager.mask_key(raw_key)
    request.state.api_key = raw_key
    request.state.client_id = raw_key
    request.state.masked_key = masked_key

    return raw_key
