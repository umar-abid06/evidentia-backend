"""Supported customer languages. English is the internal pivot language."""

from __future__ import annotations

from typing import Literal, TypeGuard, get_args

Language = Literal["en", "fr", "de", "es", "ar", "ur", "zh"]

SUPPORTED_LANGUAGES: tuple[Language, ...] = get_args(Language)
PIVOT_LANGUAGE: Language = "en"

LANGUAGE_NAMES: dict[Language, str] = {
    "en": "English",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "ar": "Arabic",
    "ur": "Urdu",
    "zh": "Chinese (Simplified)",
}


def is_language(value: object) -> TypeGuard[Language]:
    return value in SUPPORTED_LANGUAGES


def as_language(value: object, default: Language = PIVOT_LANGUAGE) -> Language:
    return value if is_language(value) else default
