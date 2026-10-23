"""OpenAI adapters. All vendor specifics stay in this module."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import cast

import openai
from openai import AsyncOpenAI, omit
from openai.types.chat import ChatCompletionMessageParam

from evidentia.ai.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderOutputTruncatedError,
    ProviderRateLimitedError,
    ProviderRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from evidentia.ai.types import ChatRequest, ChatResult, EmbeddingResult


@contextmanager
def _translate_errors() -> Iterator[None]:
    try:
        yield
    except openai.RateLimitError as exc:
        raise ProviderRateLimitedError(str(exc)) from exc
    except openai.APITimeoutError as exc:
        raise ProviderTimeoutError(str(exc)) from exc
    except openai.APIConnectionError as exc:
        raise ProviderUnavailableError(str(exc)) from exc
    except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
        raise ProviderAuthError(str(exc)) from exc
    except openai.APIStatusError as exc:
        if exc.status_code >= 500 or exc.status_code == 408:
            raise ProviderUnavailableError(str(exc)) from exc
        raise ProviderRequestError(str(exc)) from exc
    except openai.OpenAIError as exc:
        raise ProviderError(str(exc)) from exc


class OpenAIChatModel:
    def __init__(self, client: AsyncOpenAI, model: str, *, timeout_s: float) -> None:
        self._client = client
        self._model = model
        self._timeout_s = timeout_s

    @property
    def default_model(self) -> str:
        return self._model

    async def complete(self, request: ChatRequest) -> ChatResult:
        messages = cast(
            list[ChatCompletionMessageParam],
            [{"role": m.role, "content": m.content} for m in request.messages],
        )
        with _translate_errors():
            response = await self._client.chat.completions.create(
                model=request.model or self._model,
                messages=messages,
                response_format={"type": "json_object"} if request.json_mode else omit,
                max_completion_tokens=request.max_output_tokens or omit,
                timeout=self._timeout_s,
            )
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise ProviderOutputTruncatedError(
                f"{response.model} stopped at its output limit during {request.stage}."
            )
        usage = response.usage
        return ChatResult(
            text=choice.message.content or "",
            model=response.model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )


class OpenAIEmbeddingModel:
    def __init__(
        self, client: AsyncOpenAI, model: str, *, dimensions: int, timeout_s: float
    ) -> None:
        self._client = client
        self._model = model
        self._dimensions = dimensions
        self._timeout_s = timeout_s

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        with _translate_errors():
            response = await self._client.embeddings.create(
                model=self._model,
                input=list(texts),
                dimensions=self._dimensions,
                timeout=self._timeout_s,
            )
        ordered = sorted(response.data, key=lambda item: item.index)
        return EmbeddingResult(
            vectors=[list(item.embedding) for item in ordered],
            model=response.model,
            prompt_tokens=response.usage.prompt_tokens,
            dimensions=self._dimensions,
        )
