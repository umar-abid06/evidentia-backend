from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

CREDENTIALS = {"email": "Lena@Example.com", "password": "a-long-passphrase"}


async def test_register_login_and_read_profile(api: AsyncClient) -> None:
    created = await api.post("/api/v1/auth/register", json={**CREDENTIALS, "country_code": "de"})
    assert created.status_code == 201
    assert created.json()["email"] == "lena@example.com"
    assert created.json()["country_code"] == "DE"

    login = await api.post("/api/v1/auth/login", json=CREDENTIALS)
    assert login.status_code == 200
    tokens = login.json()

    me = await api.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["role"] == "customer"

    refreshed = await api.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200


async def test_duplicate_email_conflicts(api: AsyncClient) -> None:
    await api.post("/api/v1/auth/register", json=CREDENTIALS)
    again = await api.post("/api/v1/auth/register", json=CREDENTIALS)
    assert again.status_code == 409
    assert again.json()["code"] == "email_taken"


async def test_wrong_password_is_rejected(api: AsyncClient) -> None:
    await api.post("/api/v1/auth/register", json=CREDENTIALS)
    login = await api.post(
        "/api/v1/auth/login", json={**CREDENTIALS, "password": "not-the-right-one"}
    )
    assert login.status_code == 401
    assert login.json()["code"] == "invalid_credentials"
