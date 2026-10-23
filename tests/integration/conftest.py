"""Fixtures for tests that need PostgreSQL + pgvector.

Set ``TEST_DATABASE_URL`` (e.g. the compose ``db`` service) to run them; they are
skipped otherwise. Each test gets a freshly created schema.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from evidentia.core.config import DatabaseSettings, Settings
from evidentia.db.registry import load_models
from evidentia.db.session import Database
from evidentia.main import create_app


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


@pytest.fixture
async def db(database_url: str) -> AsyncIterator[Database]:
    database = Database(DatabaseSettings(url=database_url), null_pool=True)
    metadata = load_models()
    async with database.engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)
    yield database
    await database.dispose()


@pytest.fixture
async def api(settings: Settings, db: Database) -> AsyncIterator[AsyncClient]:
    application = create_app(settings)
    application.state.db = db
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as http:
        yield http
