from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from evidentia.core.config import AuthSettings, Settings
from evidentia.core.errors import UnauthorizedError
from evidentia.core.security import create_token, decode_token, hash_password, verify_password
from evidentia.modules.auth.dependencies import get_current_user
from evidentia.modules.auth.models import User, UserRole

CONFIG = AuthSettings()


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("correct horse battery")
    assert hashed != "correct horse battery"
    assert verify_password("correct horse battery", hashed)
    assert not verify_password("wrong password!!", hashed)


def test_missing_account_never_verifies() -> None:
    assert not verify_password("anything at all", None)


def test_token_roundtrip_carries_claims() -> None:
    user_id = uuid.uuid4()
    claims = decode_token(create_token(user_id, "admin", "access", CONFIG), "access", CONFIG)
    assert claims.sub == user_id
    assert claims.role == "admin"


def test_refresh_token_is_rejected_as_access_token() -> None:
    token = create_token(uuid.uuid4(), "customer", "refresh", CONFIG)
    with pytest.raises(UnauthorizedError):
        decode_token(token, "access", CONFIG)


def test_expired_token_is_rejected() -> None:
    issued = datetime.now(UTC) - timedelta(hours=2)
    token = create_token(uuid.uuid4(), "customer", "access", CONFIG, now=issued)
    with pytest.raises(UnauthorizedError) as excinfo:
        decode_token(token, "access", CONFIG)
    assert excinfo.value.code == "token_expired"


def test_production_refuses_default_jwt_secret() -> None:
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(environment="production")


async def test_me_requires_a_bearer_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_me_returns_the_current_user(app: FastAPI, client: AsyncClient) -> None:
    user = User(
        id=uuid.uuid4(),
        email="ana@example.com",
        password_hash="x",
        role=UserRole.CUSTOMER,
        preferred_language="de",
        created_at=datetime.now(UTC),
    )
    app.dependency_overrides[get_current_user] = lambda: user
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == "ana@example.com"
    assert response.json()["preferred_language"] == "de"
