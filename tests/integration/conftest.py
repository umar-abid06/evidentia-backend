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
from evidentia.core.security import create_token, hash_password
from evidentia.db.registry import load_models
from evidentia.db.session import Database
from evidentia.main import create_app
from evidentia.modules.auth.models import User, UserRole


class _NullRedis:
    async def ping(self) -> bool:
        return True


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
    application.state.redis = _NullRedis()
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as http:
        yield http


async def _user_headers(db: Database, settings: Settings, role: UserRole) -> dict[str, str]:
    async with db.session() as session:
        user = User(
            email=f"{role.value}@example.com",
            password_hash=hash_password("integration-pass"),
            role=role,
            country_code="DE",
        )
        session.add(user)
        await session.commit()
    token = create_token(user.id, role.value, "access", settings.auth)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def admin_headers(db: Database, settings: Settings) -> dict[str, str]:
    return await _user_headers(db, settings, UserRole.ADMIN)


@pytest.fixture
async def customer_headers(db: Database, settings: Settings) -> dict[str, str]:
    return await _user_headers(db, settings, UserRole.CUSTOMER)
