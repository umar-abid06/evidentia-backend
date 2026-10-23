from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from evidentia.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from evidentia.db.types import str_enum


class GenerationStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class HealthGuide(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One guide per destination, version and language, shared by every trip there."""

    __tablename__ = "health_guides"
    __table_args__ = (
        UniqueConstraint("country_code", "region_code", "language", "version"),
        Index("ix_health_guides_status", "status"),
    )

    country_code: Mapped[str] = mapped_column(String(2))
    region_code: Mapped[str] = mapped_column(String(10), default="")
    language: Mapped[str] = mapped_column(String(5), default="en")
    version: Mapped[int]
    status: Mapped[GenerationStatus] = mapped_column(
        str_enum(GenerationStatus), default=GenerationStatus.PENDING
    )
    content: Mapped[dict[str, Any] | None]
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    generated_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]
    claimed_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(default=0)
    error: Mapped[dict[str, Any] | None]


class Trip(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trips"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    destination_country: Mapped[str] = mapped_column(String(2))
    destination_region: Mapped[str] = mapped_column(String(10), default="")
    start_date: Mapped[date]
    end_date: Mapped[date]
    health_guide_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("health_guides.id", ondelete="SET NULL")
    )

    health_guide: Mapped[HealthGuide | None] = relationship(lazy="joined")


class JetLagPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "jetlag_plans"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    trip_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("trips.id", ondelete="SET NULL"))
    segments: Mapped[list[Any]]
    metrics: Mapped[dict[str, Any]]
    plan: Mapped[dict[str, Any] | None]
    schema_version: Mapped[str] = mapped_column(String(30))
    status: Mapped[GenerationStatus] = mapped_column(
        str_enum(GenerationStatus), default=GenerationStatus.PENDING
    )
    model: Mapped[str | None] = mapped_column(String(100))
    attempts: Mapped[int] = mapped_column(default=0)
    error: Mapped[dict[str, Any] | None]
