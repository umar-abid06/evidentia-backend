"""Single entry point for every model call: tiering, retries, parsing, telemetry."""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from evidentia.ai.errors import AIError, ProviderNotConfiguredError
from evidentia.ai.retry import DEFAULT_DELAYS, retry_transient
from evidentia.ai.structured import parse_json_object
from evidentia.ai.telemetry import LLMCallRecord, LLMTelemetry
from evidentia.ai.types import (
    ChatMessage,
    ChatModel,
    ChatRequest,
    ChatResult,
    EmbeddingModel,
    EmbeddingResult,
    ModelTier,
)

_tracer = trace.get_tracer("evidentia.ai")


class AIGateway:
    """Routes work to the right model tier.

    ``reasoning`` handles document understanding (extraction, classification,
    organisation); ``fast`` handles chat and translation. Every call is retried on
    transient errors only and recorded in telemetry, success or failure.
    """

    def __init__(
        self,
        *,
        reasoning: ChatModel,
        fast: ChatModel,
        embeddings: EmbeddingModel,
        telemetry: LLMTelemetry | None = None,
        retry_delays: Sequence[float] = DEFAULT_DELAYS,
        configured: bool = True,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._tiers: dict[ModelTier, ChatModel] = {"reasoning": reasoning, "fast": fast}
        self.embeddings = embeddings
        self.telemetry = telemetry or LLMTelemetry()
        self._delays = tuple(retry_delays)
        self._configured = configured
        self._sleep = sleep

    @property
    def is_configured(self) -> bool:
        return self._configured

    def model_for(self, tier: ModelTier) -> str:
        return self._tiers[tier].default_model

    def with_telemetry(self, telemetry: LLMTelemetry) -> AIGateway:
        """Same adapters, separate telemetry: one unit of work, one set of records."""
        clone = copy.copy(self)
        clone.telemetry = telemetry
        return clone

    async def chat(self, request: ChatRequest, *, tier: ModelTier) -> ChatResult:
        self._ensure_configured()
        backend = self._tiers[tier]
        model = request.model or backend.default_model
        started = time.perf_counter()
        with _tracer.start_as_current_span(f"llm {request.stage}") as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.request.model", model)
            span.set_attribute("evidentia.llm.stage", request.stage)
            span.set_attribute("evidentia.llm.tier", tier)
            try:
                result, retries = await retry_transient(
                    lambda: backend.complete(request), delays=self._delays, sleep=self._sleep
                )
            except AIError as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                self._record(request.stage, model, started, request.payload_bytes, exc=exc)
                raise
            span.set_attribute("gen_ai.response.model", result.model)
            span.set_attribute("gen_ai.usage.input_tokens", result.prompt_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", result.completion_tokens)
            span.set_attribute("evidentia.llm.retries", retries)
        self._record(
            request.stage,
            result.model,
            started,
            request.payload_bytes,
            retries=retries,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )
        return result

    async def complete_json(
        self,
        *,
        stage: str,
        messages: Sequence[ChatMessage],
        tier: ModelTier = "reasoning",
        model: str | None = None,
        max_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        request = ChatRequest(
            stage=stage,
            messages=messages,
            json_mode=True,
            model=model,
            max_output_tokens=max_output_tokens,
        )
        result = await self.chat(request, tier=tier)
        return parse_json_object(result.text, stage=stage)

    async def complete_text(
        self,
        *,
        stage: str,
        messages: Sequence[ChatMessage],
        tier: ModelTier = "fast",
        model: str | None = None,
        max_output_tokens: int | None = None,
    ) -> str:
        request = ChatRequest(
            stage=stage, messages=messages, model=model, max_output_tokens=max_output_tokens
        )
        return (await self.chat(request, tier=tier)).text

    async def embed(self, *, stage: str, texts: Sequence[str]) -> EmbeddingResult:
        self._ensure_configured()
        payload = sum(len(text.encode()) for text in texts)
        started = time.perf_counter()
        with _tracer.start_as_current_span(f"llm {stage}") as span:
            span.set_attribute("gen_ai.operation.name", "embeddings")
            span.set_attribute("gen_ai.request.model", self.embeddings.model)
            span.set_attribute("evidentia.llm.batch_size", len(texts))
            try:
                result, retries = await retry_transient(
                    lambda: self.embeddings.embed(texts), delays=self._delays, sleep=self._sleep
                )
            except AIError as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                self._record(stage, self.embeddings.model, started, payload, exc=exc)
                raise
            span.set_attribute("gen_ai.usage.input_tokens", result.prompt_tokens)
        self._record(
            stage,
            result.model,
            started,
            payload,
            retries=retries,
            prompt_tokens=result.prompt_tokens,
        )
        return result

    def _ensure_configured(self) -> None:
        if not self._configured:
            raise ProviderNotConfiguredError("No model provider credentials are configured.")

    def _record(
        self,
        stage: str,
        model: str,
        started: float,
        payload_bytes: int,
        *,
        retries: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        exc: AIError | None = None,
    ) -> None:
        self.telemetry.record(
            LLMCallRecord(
                stage=stage,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                payload_bytes=payload_bytes,
                retries=exc.attempts - 1 if exc else retries,
                success=exc is None,
                error_type=type(exc).__name__ if exc else None,
            )
        )
