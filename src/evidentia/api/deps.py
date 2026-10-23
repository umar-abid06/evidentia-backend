"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from evidentia.ai.gateway import AIGateway
from evidentia.core.config import Settings
from evidentia.db.session import Database
from evidentia.integrations.storage import ObjectStorage


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_database(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


def get_storage(request: Request) -> ObjectStorage:
    storage: ObjectStorage = request.app.state.storage
    return storage


def get_ai_gateway(request: Request) -> AIGateway:
    gateway: AIGateway = request.app.state.ai
    return gateway


async def get_session(
    db: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    async with db.session() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
StorageDep = Annotated[ObjectStorage, Depends(get_storage)]
AIGatewayDep = Annotated[AIGateway, Depends(get_ai_gateway)]
