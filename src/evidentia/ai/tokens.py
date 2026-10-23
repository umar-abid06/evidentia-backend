"""Token counting with the model's own tokenizer, and a safe fallback offline."""

from __future__ import annotations

import threading
from typing import Any

import structlog

CHAT_ENCODING = "o200k_base"
EMBEDDING_ENCODING = "cl100k_base"

log = structlog.get_logger(__name__)

_lock = threading.Lock()
_encodings: dict[str, Any | None] = {}


def _encoding(name: str) -> Any | None:
    with _lock:
        if name not in _encodings:
            try:
                import tiktoken  # noqa: PLC0415 - optional at import time

                _encodings[name] = tiktoken.get_encoding(name)
            except Exception as exc:  # noqa: BLE001 - offline / missing cache
                log.warning("tokenizer_unavailable", encoding=name, error=type(exc).__name__)
                _encodings[name] = None
        return _encodings[name]


def count_tokens(text: str, *, encoding: str = CHAT_ENCODING) -> int:
    enc = _encoding(encoding)
    if enc is None:
        return max(1, len(text) // 4)
    return len(enc.encode(text, disallowed_special=()))
