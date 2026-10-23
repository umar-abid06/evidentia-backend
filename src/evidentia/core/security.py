"""Password hashing and signed access/refresh tokens."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from pwdlib import PasswordHash
from pydantic import BaseModel, ValidationError

from evidentia.core.config import AuthSettings
from evidentia.core.errors import UnauthorizedError

TokenType = Literal["access", "refresh"]

_hasher = PasswordHash.recommended()
# Verified when the account does not exist so response timing does not reveal it.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


class TokenClaims(BaseModel):
    sub: uuid.UUID
    role: str
    typ: TokenType
    iss: str
    iat: datetime
    exp: datetime
    jti: str


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if password_hash is None:
        _hasher.verify(password, _DUMMY_HASH)
        return False
    return _hasher.verify(password, password_hash)


def create_token(
    subject: uuid.UUID,
    role: str,
    kind: TokenType,
    config: AuthSettings,
    *,
    now: datetime | None = None,
) -> str:
    issued = now or datetime.now(UTC)
    ttl = (
        timedelta(minutes=config.access_ttl_minutes)
        if kind == "access"
        else timedelta(days=config.refresh_ttl_days)
    )
    payload = {
        "sub": str(subject),
        "role": role,
        "typ": kind,
        "iss": config.issuer,
        "iat": issued,
        "exp": issued + ttl,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, config.jwt_secret.get_secret_value(), algorithm=config.jwt_algorithm)


def decode_token(token: str, expected: TokenType, config: AuthSettings) -> TokenClaims:
    try:
        raw = jwt.decode(
            token,
            config.jwt_secret.get_secret_value(),
            algorithms=[config.jwt_algorithm],
            issuer=config.issuer,
            options={"require": ["exp", "iat", "sub", "typ"]},
        )
        claims = TokenClaims.model_validate(raw)
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Token has expired.", code="token_expired") from exc
    except (jwt.InvalidTokenError, ValidationError) as exc:
        raise UnauthorizedError("Token is invalid.", code="token_invalid") from exc
    if claims.typ != expected:
        raise UnauthorizedError("Wrong token type.", code="token_invalid")
    return claims
