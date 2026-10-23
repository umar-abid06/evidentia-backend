"""Where request handlers get their AIGateway from."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from evidentia.ai.gateway import AIGateway


class GatewaySource(Protocol):
    async def get(self, session: AsyncSession) -> AIGateway: ...


class StaticGatewaySource:
    """A fixed gateway (tests, offline development)."""

    def __init__(self, gateway: AIGateway) -> None:
        self.gateway = gateway

    async def get(self, session: AsyncSession) -> AIGateway:
        return self.gateway
