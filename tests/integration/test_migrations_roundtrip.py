from __future__ import annotations

import asyncio

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from evidentia.db.registry import load_models
from tests.test_migrations import alembic_config

pytestmark = pytest.mark.integration


async def _reset(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(load_models().drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    await engine.dispose()


async def _tables(url: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    await engine.dispose()
    return names


def test_upgrade_and_downgrade_roundtrip(database_url: str) -> None:
    asyncio.run(_reset(database_url))
    config = alembic_config(database_url)
    command.upgrade(config, "head")
    assert set(load_models().tables) <= asyncio.run(_tables(database_url))
    command.downgrade(config, "base")
    assert asyncio.run(_tables(database_url)) <= {"alembic_version"}
