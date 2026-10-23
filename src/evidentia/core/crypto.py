"""Symmetric encryption for secrets at rest (Fernet; rotation via MultiFernet)."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from pydantic import SecretStr

from evidentia.core.errors import ServiceUnavailableError


class SecretBox:
    """The first key encrypts; every key decrypts, so keys rotate without downtime."""

    def __init__(self, keys: list[SecretStr]) -> None:
        self._fernet = (
            MultiFernet([Fernet(key.get_secret_value().encode()) for key in keys]) if keys else None
        )

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plaintext: str) -> str:
        return self._require().encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._require().decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ServiceUnavailableError(
                "Stored secret cannot be decrypted with the configured keys.",
                code="secret_undecryptable",
            ) from exc

    def _require(self) -> MultiFernet:
        if self._fernet is None:
            raise ServiceUnavailableError(
                "EVIDENTIA_SECURITY__ENCRYPTION_KEYS is not configured.",
                code="encryption_not_configured",
            )
        return self._fernet


def generate_key() -> str:
    return Fernet.generate_key().decode()
