"""Provider-neutral request/response types and adapter protocols."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

Role = Literal["system", "user", "assistant"]
ModelTier = Literal["reasoning", "fast"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class ChatRequest:
    stage: str
    messages: Sequence[ChatMessage]
    json_mode: bool = False
    model: str | None = None
    max_output_tokens: int | None = None

    @property
    def payload_bytes(self) -> int:
        return sum(len(message.content.encode()) for message in self.messages)


@dataclass(frozen=True, slots=True)
class ChatResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    prompt_tokens: int = 0
    dimensions: int = field(default=0)


class ChatModel(Protocol):
    """One chat-completion backend (a vendor + default model)."""

    @property
    def default_model(self) -> str: ...

    async def complete(self, request: ChatRequest) -> ChatResult: ...


class EmbeddingModel(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult: ...
