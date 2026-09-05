"""Static Key Manager for client authentication and secure key masking."""

from typing import List, Optional
from app.core.config import Settings, get_settings


class KeyManager:
    """Static key manager that validates client keys against environment settings

    and safely masks them for logging and audits.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    def validate_key(self, api_key: Optional[str]) -> bool:
        """Validate whether the provided client API key matches configured keys."""
        if not api_key or not isinstance(api_key, str):
            return False
        return self.settings.is_valid_gateway_key(api_key.strip())

    @staticmethod
    def mask_key(api_key: Optional[str]) -> str:
        """Mask an API key for safe logging or display (e.g. 'gw-test-****').

        Preserves prefix or shows first few chars followed by asterisks.
        Never reveals the full secret in plain text.
        """
        if not api_key:
            return "[EMPTY]"
        
        cleaned = api_key.strip()
        if len(cleaned) <= 4:
            return "****"

        # If key contains dashes (e.g. 'gw-test-key-1'), retain prefix before the final token
        if "-" in cleaned:
            parts = cleaned.rsplit("-", 1)
            prefix = parts[0]
            return f"{prefix}-****"

        # Otherwise mask all but the first 4 characters
        return f"{cleaned[:4]}****"

    def get_client_id(self, api_key: Optional[str]) -> str:
        """Return a client identifier suitable for logging and rate-limiting."""
        if not api_key:
            return "anonymous"
        return self.mask_key(api_key)

    def list_masked_keys(self) -> List[str]:
        """Return the list of configured gateway API keys in masked form."""
        return [self.mask_key(k) for k in self.settings.GATEWAY_API_KEYS]


# Singleton instance
key_manager = KeyManager()
