"""Bridges synchronous Celery tasks to the async service layer."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import structlog
from redis.asyncio import Redis

from evidentia.core.config import Settings, get_settings
from evidentia.db.registry import load_models
from evidentia.db.session import Database
from evidentia.modules.notifications.events import Notifier, RedisNotifier


@dataclass(slots=True)
class WorkerContext:
    settings: Settings
    db: Database


@asynccontextmanager
async def worker_context() -> AsyncIterator[WorkerContext]:
    """One job, one event loop, one non-pooled engine; nothing leaks across jobs."""
    settings = get_settings()
    load_models()
    db = Database(settings.database, null_pool=True)
    try:
        yield WorkerContext(settings=settings, db=db)
    finally:
        await db.dispose()


@asynccontextmanager
async def notifier(settings: Settings) -> AsyncIterator[Notifier]:
    redis = Redis.from_url(settings.redis.url, decode_responses=True)
    try:
        yield RedisNotifier(redis)
    finally:
        await redis.aclose()


def run_job[T](job: Callable[[WorkerContext], Awaitable[T]], **log_context: object) -> T:
    async def main() -> T:
        structlog.contextvars.bind_contextvars(**log_context)
        try:
            async with worker_context() as ctx:
                return await job(ctx)
        finally:
            structlog.contextvars.clear_contextvars()

    return asyncio.run(main())
