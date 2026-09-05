"""Chat completion request and response schemas."""

import time
import uuid
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    role: Role
    content: str = Field(..., min_length=1, description="Content of the message")


class TokenUsage(BaseModel):
    prompt_tokens: int = Field(default=0, description="Tokens used by prompt")
    completion_tokens: int = Field(default=0, description="Tokens generated in completion")
    total_tokens: int = Field(default=0, description="Total tokens used")


class ChatChoice(BaseModel):
    index: int = 0
    message: Message
    finish_reason: str = "stop"


class ChatRequest(BaseModel):
    model: Optional[str] = Field(default=None, description="Model identifier (optional, uses provider default if omitted)")
    provider: Optional[str] = Field(default=None, description="Target provider: 'ollama', 'groq', or 'mock'")
    messages: List[Message] = Field(..., min_length=1, description="List of conversation messages")
    temperature: Optional[float] = Field(default=0.7, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: Optional[int] = Field(default=None, gt=0, description="Maximum tokens to generate")
    stream: bool = Field(default=False, description="Whether to stream back partial progress")


class ChatResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    created: int = Field(default_factory=lambda: int(time.time()))
    provider: str
    model: str
    choices: List[ChatChoice]
    usage: TokenUsage
    latency_ms: float = Field(default=0.0, description="End-to-end execution latency in milliseconds")
