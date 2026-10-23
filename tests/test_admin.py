from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from evidentia.core.config import LLMSettings, SecuritySettings, Settings
from evidentia.core.crypto import SecretBox, generate_key
from evidentia.core.errors import ServiceUnavailableError
from evidentia.modules.admin.models import SystemSetting
from evidentia.modules.admin.store import (
    PROMPT_CATALOG,
    CachedGatewaySource,
    SettingsStore,
    mask,
)
from evidentia.modules.auth.dependencies import get_current_user
from evidentia.modules.auth.models import User, UserRole


def test_secrets_roundtrip_and_survive_key_rotation() -> None:
    old, new = generate_key(), generate_key()
    token = SecretBox([SecretStr(old)]).encrypt("sk-live-123")
    assert "sk-live" not in token
    rotated = SecretBox([SecretStr(new), SecretStr(old)])
    assert rotated.decrypt(token) == "sk-live-123"
    assert rotated.decrypt(rotated.encrypt("x")) == "x"


def test_secret_box_requires_a_key() -> None:
    with pytest.raises(ServiceUnavailableError):
        SecretBox([]).encrypt("value")


def test_secrets_are_masked() -> None:
    assert mask("sk-abcdefghijklmnop") == "****mnop"
    assert mask("short") == "****"


def test_production_requires_an_encryption_key() -> None:
    with pytest.raises(ValueError, match="ENCRYPTION_KEYS"):
        Settings.model_validate({"environment": "production", "auth": {"jwt_secret": "x" * 40}})


def test_every_prompt_is_versioned_in_the_catalog() -> None:
    assert PROMPT_CATALOG["assistant.answer"].version == "1.14"
    assert PROMPT_CATALOG["extraction.organizer"].version == "3"
    assert set(PROMPT_CATALOG) >= {
        "extraction.discovery",
        "extraction.classifier",
        "localization.translator",
        "travel.health_guide",
        "travel.jetlag_plan",
    }


class _FakeSession:
    def __init__(self) -> None:
        self.rows: dict[str, SystemSetting] = {}

    async def get(self, model: Any, key: str) -> SystemSetting | None:
        return self.rows.get(key)


async def test_database_value_wins_over_environment() -> None:
    key = generate_key()
    settings = Settings(
        llm=LLMSettings(openai_api_key=SecretStr("env-key")),
        security=SecuritySettings(encryption_keys=[SecretStr(key)]),
    )
    box = SecretBox(settings.security.encryption_keys)
    session = _FakeSession()
    store = SettingsStore(session, settings, box)  # type: ignore[arg-type]
    assert await store.get("llm.openai_api_key") == "env-key"
    session.rows["llm.openai_api_key"] = SystemSetting(
        key="llm.openai_api_key", value=box.encrypt("db-key"), is_secret=True
    )
    assert await store.get("llm.openai_api_key") == "db-key"


async def test_gateway_is_rebuilt_when_the_key_rotates() -> None:
    key = generate_key()
    settings = Settings(security=SecuritySettings(encryption_keys=[SecretStr(key)]))
    source = CachedGatewaySource(settings, ttl_s=0)
    session = _FakeSession()
    first = await source.get(session)  # type: ignore[arg-type]
    assert not first.is_configured
    assert await source.get(session) is first  # type: ignore[arg-type]
    session.rows["llm.openai_api_key"] = SystemSetting(
        key="llm.openai_api_key",
        value=SecretBox([SecretStr(key)]).encrypt("sk-new"),
        is_secret=True,
    )
    second = await source.get(session)  # type: ignore[arg-type]
    assert second is not first
    assert second.is_configured


async def test_configuration_is_admin_only(app: FastAPI, client: AsyncClient) -> None:
    app.dependency_overrides[get_current_user] = lambda: User(
        id=uuid.uuid4(),
        email="r@example.com",
        password_hash="x",
        role=UserRole.REVIEWER,
        created_at=datetime.now(UTC),
    )
    response = await client.put("/api/v1/admin/settings/llm.openai_api_key", json={"value": "x"})
    assert response.status_code == 403
