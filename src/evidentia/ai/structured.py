"""Strict JSON parsing, contract validation and storage-safe sanitising."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from evidentia.ai.errors import ContractViolationError, MalformedJSONError, NotAJSONObjectError

PREVIEW_CHARS = 400

# NUL, non-printing C0 controls (tab/newline/CR kept) and lone surrogates cannot be
# stored in PostgreSQL text/JSONB columns.
_UNSTORABLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]")


def sanitize_text(value: str) -> str:
    return _UNSTORABLE.sub("", value)


def sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {sanitize_text(str(key)): sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def preview(text: str) -> str:
    return text if len(text) <= PREVIEW_CHARS else f"{text[:PREVIEW_CHARS]}…"


def parse_json_object(text: str, *, stage: str | None = None) -> dict[str, Any]:
    """Parses a model response that must be exactly one JSON object."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MalformedJSONError(
            f"Model returned malformed JSON ({exc.msg} at char {exc.pos}).",
            preview=preview(text),
            stage=stage,
        ) from exc
    if not isinstance(data, dict):
        raise NotAJSONObjectError(
            f"Model returned a JSON {type(data).__name__}, expected an object.",
            preview=preview(text),
            stage=stage,
        )
    cleaned: dict[str, Any] = sanitize(data)
    return cleaned


def validate_contract[M: BaseModel](
    model: type[M], data: dict[str, Any], *, stage: str | None = None
) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        errors = [
            {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]}
            for err in exc.errors()[:20]
        ]
        raise ContractViolationError(
            f"{stage or 'model'} output violates the {model.__name__} contract.",
            errors=errors,
            preview=preview(json.dumps(data, default=str)),
            stage=stage,
        ) from exc
