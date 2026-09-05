"""Provider metadata schemas."""

from typing import Optional
from pydantic import BaseModel


class ModelInfo(BaseModel):
    name: str
    provider: str
    description: Optional[str] = None
    is_local: bool = False
    context_length: Optional[int] = None
