"""Async engine and session lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from evidentia.core.config import DatabaseSettings


class Database:
    """Owns one engine and its session factory.

    The API process keeps a pooled engine for its lifetime. Background workers
    build one with ``null_pool=True`` per job so connections never cross event loops.
    """

    def __init__(self, config: DatabaseSettings, *, null_pool: bool = False) -> None:
        if null_pool:
            self.engine: AsyncEngine = create_async_engine(
                config.url, echo=config.echo, poolclass=NullPool
            )
        else:
            self.engine = create_async_engine(
                config.url,
                echo=config.echo,
                pool_size=config.pool_size,
                max_overflow=config.max_overflow,
                pool_timeout=config.pool_timeout_s,
                pool_pre_ping=True,
            )
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            try:
                yield session
            except BaseException:
                await session.rollback()
                raise

    async def ping(self) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()
