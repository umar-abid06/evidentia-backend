"""Operator notifications: produced by workers, delivered to admins in real time."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal, Protocol

import structlog
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from evidentia.modules.documents.models import Document, DocumentStatus, ReviewStatus
from evidentia.modules.knowledge.models import PolicyVersion, VersionStatus

ADMIN_CHANNEL = "evidentia:notifications:admin"

Level = Literal["success", "warning", "error"]

log = structlog.get_logger(__name__)


class Notification(BaseModel):
    level: Level
    title: str
    message: str
    entity_type: Literal["document", "policy_version"]
    entity_id: uuid.UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Notifier(Protocol):
    async def publish(self, notification: Notification) -> None: ...


class RedisNotifier:
    def __init__(self, redis: Redis, channel: str = ADMIN_CHANNEL) -> None:
        self.redis = redis
        self.channel = channel

    async def publish(self, notification: Notification) -> None:
        try:
            await self.redis.publish(self.channel, notification.model_dump_json())
        except Exception:  # delivery is best effort; the state lives in the database
            log.exception("notification_publish_failed", title=notification.title)


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[Notification] = []

    async def publish(self, notification: Notification) -> None:
        self.sent.append(notification)


def for_document(document: Document) -> Notification:
    name = document.original_filename
    if document.status is DocumentStatus.FAILED:
        reason = (document.error or {}).get("message", "Unknown error")
        return Notification(
            level="error",
            title=f"Processing failed: {name}",
            message=str(reason),
            entity_type="document",
            entity_id=document.id,
        )
    if document.review_status is ReviewStatus.NEEDS_REVIEW:
        codes = ", ".join(r.get("code", "") for r in document.review_reasons) or "manual check"
        return Notification(
            level="warning",
            title=f"Review needed: {name}",
            message=f"Held for review ({codes}).",
            entity_type="document",
            entity_id=document.id,
        )
    published = document.review_status is ReviewStatus.APPROVED
    return Notification(
        level="success",
        title=f"Processed: {name}",
        message="Approved automatically and queued for publishing."
        if published
        else "Extraction finished.",
        entity_type="document",
        entity_id=document.id,
    )


def for_version(version: PolicyVersion) -> Notification:
    if version.status is VersionStatus.READY:
        return Notification(
            level="success",
            title=f"Version {version.version_number} is live",
            message=f"{version.chunk_count} facts searchable, {version.embedding_calls} "
            "embedding call(s).",
            entity_type="policy_version",
            entity_id=version.id,
        )
    return Notification(
        level="error",
        title=f"Publishing version {version.version_number} failed",
        message=str((version.error or {}).get("message", "Unknown error")),
        entity_type="policy_version",
        entity_id=version.id,
    )
