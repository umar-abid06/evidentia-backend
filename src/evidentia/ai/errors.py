"""Typed failures raised by the AI runtime."""

from __future__ import annotations

from typing import Any


class AIError(Exception):
    """Base class for every AI runtime failure."""

    transient: bool = False

    def __init__(self, message: str, *, attempts: int = 1) -> None:
        super().__init__(message)
        self.attempts = attempts


class ProviderError(AIError):
    """The model provider rejected or failed a request."""


class ProviderRateLimitedError(ProviderError):
    transient = True


class ProviderTimeoutError(ProviderError):
    transient = True


class ProviderUnavailableError(ProviderError):
    transient = True


class ProviderAuthError(ProviderError):
    """Missing or invalid credentials. Never retried."""


class ProviderRequestError(ProviderError):
    """The request itself is invalid (bad input, unknown model). Never retried."""


class ProviderOutputTruncatedError(ProviderError):
    """The model hit its output limit, so the payload is incomplete."""


class ProviderNotConfiguredError(ProviderError):
    """No usable credentials are configured for the selected provider."""


class ModelOutputError(AIError):
    """The model answered, but not with what the contract demands."""

    def __init__(self, message: str, *, preview: str = "", stage: str | None = None) -> None:
        super().__init__(message)
        self.preview = preview
        self.stage = stage


class MalformedJSONError(ModelOutputError):
    pass


class NotAJSONObjectError(ModelOutputError):
    pass


class ContractViolationError(ModelOutputError):
    def __init__(
        self,
        message: str,
        *,
        errors: list[dict[str, Any]] | None = None,
        preview: str = "",
        stage: str | None = None,
    ) -> None:
        super().__init__(message, preview=preview, stage=stage)
        self.errors = errors or []
