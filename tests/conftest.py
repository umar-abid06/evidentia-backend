from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from evidentia.core.config import Settings
from evidentia.core.tasks import RecordingDispatcher, set_dispatcher
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
def dispatcher() -> RecordingDispatcher:
    recorder = RecordingDispatcher()
    set_dispatcher(recorder)
    return recorder


@pytest.fixture
def settings() -> Settings:
    return Settings(environment="test", log_json=False, log_level="WARNING")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    application = create_app(settings)
    application.state.db = _HealthyDatabase()
    application.state.redis = _HealthyRedis()
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
