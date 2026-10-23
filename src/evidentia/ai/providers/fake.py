"""Deterministic offline adapters for local development and tests."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from itertools import pairwise
from typing import Any

from evidentia.ai.types import ChatRequest, ChatResult, EmbeddingResult

Responder = Callable[[ChatRequest], str | dict[str, Any]]

_WORD = re.compile(r"[a-z0-9]+")


class ScriptedChatModel:
    """Answers each request with the responder registered for its stage.

    Stages without a responder get ``{}`` in JSON mode, or an empty string.
    Every request is kept in ``calls`` for assertions.
    """

    def __init__(
        self, responders: Mapping[str, Responder] | None = None, *, model: str = "fake-chat"
    ) -> None:
        self._responders = dict(responders or {})
        self._model = model
        self.calls: list[ChatRequest] = []

    @property
    def default_model(self) -> str:
        return self._model

    def on(self, stage: str, responder: Responder) -> None:
        self._responders[stage] = responder

    async def complete(self, request: ChatRequest) -> ChatResult:
        self.calls.append(request)
        responder = self._responders.get(request.stage)
        if responder is None:
            text = "{}" if request.json_mode else ""
        else:
            answer = responder(request)
            text = answer if isinstance(answer, str) else json.dumps(answer)
        prompt_tokens = request.payload_bytes // 4
        return ChatResult(
            text=text,
            model=request.model or self._model,
            prompt_tokens=prompt_tokens,
            completion_tokens=len(text) // 4,
        )


class HashEmbeddingModel:
    """Signed feature hashing of word unigrams and bigrams, L2-normalised.

    Texts that share vocabulary get a higher cosine similarity, which is enough
    to exercise retrieval end-to-end without a provider.
    """

    def __init__(self, *, dimensions: int = 1536, model: str = "fake-hash-embedding") -> None:
        self._dimensions = dimensions
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def vector(self, text: str) -> list[float]:
        words = _WORD.findall(text.lower())
        features = words + [f"{a} {b}" for a, b in pairwise(words)]
        values = [0.0] * self._dimensions
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimensions
            values[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=[self.vector(text) for text in texts],
            model=self._model,
            prompt_tokens=sum(len(text) // 4 for text in texts),
            dimensions=self._dimensions,
        )
