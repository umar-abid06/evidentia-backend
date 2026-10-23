from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from evidentia.ai.factory import build_gateway
from evidentia.ai.source import StaticGatewaySource
from evidentia.core.config import LLMSettings, Settings
from evidentia.core.tasks import RecordingDispatcher, set_dispatcher
from evidentia.integrations.storage import InMemoryStorage
from evidentia.main import create_app


class _NoDatabaseSession:
    """Stands in for AsyncSession in unit tests; any real use fails loudly."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"unit test touched the database (session.{name})")


class _HealthyDatabase:
    async def ping(self) -> None:
        return None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[Any]:
        yield _NoDatabaseSession()

    async def dispose(self) -> None:
        return None


class _HealthyRedis:
    async def ping(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never read a developer's .env file or EVIDENTIA_* variables."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in list(os.environ):
        if name.startswith("EVIDENTIA_"):
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def dispatcher() -> RecordingDispatcher:
    recorder = RecordingDispatcher()
    set_dispatcher(recorder)
    return recorder


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        log_json=False,
        log_level="WARNING",
        llm=LLMSettings(provider="fake"),
    )


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


@pytest.fixture
def app(settings: Settings, storage: InMemoryStorage) -> FastAPI:
    application = create_app(settings)
    application.state.db = _HealthyDatabase()
    application.state.redis = _HealthyRedis()
    application.state.storage = storage
    application.state.ai = StaticGatewaySource(build_gateway(settings.llm))
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
