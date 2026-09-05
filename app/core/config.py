"""Unified Configuration Schema for Secure Multi-Provider LLM Gateway.
Supports Layer 1 (Core & Security), Layer 2 (Local Ollama), and Layer 3 (Groq & Routing).
"""

import os
from enum import Enum
from functools import lru_cache
from typing import Any, List, Optional, Set, Union
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderType(str, Enum):
    OLLAMA = "ollama"
    GROQ = "groq"
    MOCK = "mock"


class Settings(BaseSettings):
    # App Core (Layer 1)
    APP_NAME: str = "Secure Multi-Provider LLM Gateway"
    APP_ENV: str = "development"
    DEBUG: bool = False
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # Client Authentication & Rate Limiting (Layer 1)
    # Static list of valid client keys for L1-06 (configured via env or .env)
    GATEWAY_API_KEYS: Union[List[str], str] = Field(default_factory=lambda: ["gw-test-key-1", "gw-test-key-2"])

    RATE_LIMIT_PER_MINUTE: int = 60

    # Local LLM - Ollama (Layer 2)
    # Optimized for 8GB RAM host machine: qwen2.5:1.5b (fallback gemma2:2b)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_DEFAULT_MODEL: str = "qwen2.5:1.5b"
    OLLAMA_FALLBACK_MODEL: str = "gemma2:2b"
    OLLAMA_REQUEST_TIMEOUT: float = 60.0

    # Cloud Provider - Groq Free Tier (Layer 3)
    # Requires only free tier API key from https://console.groq.com (No billing required)
    GROQ_API_KEY: Optional[str] = None
    GROQ_DEFAULT_MODEL: str = "llama-3.1-8b-instant"

    # Multi-Provider Routing & Observability (Layer 3)
    DEFAULT_PROVIDER: ProviderType = ProviderType.MOCK
    ENABLE_STRUCTURED_LOGGING: bool = True
    LOG_LEVEL: str = "INFO"
    REDACT_SECRETS_IN_LOGS: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

    @field_validator("GATEWAY_API_KEYS", mode="before")
    @classmethod
    def parse_api_keys(cls, v):
        if isinstance(v, str):
            # Split comma separated string
            return [k.strip() for k in v.split(",") if k.strip()]
        return v

    def is_valid_gateway_key(self, key: Optional[str]) -> bool:
        """Validate whether the supplied client API key matches configured keys."""
        if not key:
            return False
        return key in self.GATEWAY_API_KEYS

    @property
    def valid_keys_set(self) -> Set[str]:
        return set(self.GATEWAY_API_KEYS)


@lru_cache()
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
