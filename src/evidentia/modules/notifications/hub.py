"""Fans Redis pub/sub notifications out to connected admin WebSockets."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Protocol

import structlog
from redis.asyncio import Redis

from evidentia.modules.notifications.events import ADMIN_CHANNEL

log = structlog.get_logger(__name__)


class Socket(Protocol):
    async def send_text(self, data: str) -> None: ...


class NotificationHub:
    def __init__(self, redis: Redis, channel: str = ADMIN_CHANNEL) -> None:
        self.redis = redis
        self.channel = channel
        self.clients: set[Socket] = set()
        self._task: asyncio.Task[None] | None = None

    def connect(self, socket: Socket) -> None:
        self.clients.add(socket)

    def disconnect(self, socket: Socket) -> None:
        self.clients.discard(socket)

    async def broadcast(self, data: str) -> None:
        for socket in list(self.clients):
            try:
                await socket.send_text(data)
            except Exception:  # noqa: BLE001 - a dead socket must not block the others
                self.clients.discard(socket)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._listen(), name="notification-hub")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _listen(self) -> None:
        while True:
            try:
                async with self.redis.pubsub() as pubsub:
                    await pubsub.subscribe(self.channel)
                    async for message in pubsub.listen():
                        if message.get("type") == "message":
                            await self.broadcast(str(message["data"]))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("notification_hub_reconnecting")
                await asyncio.sleep(2)
