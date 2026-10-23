from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from evidentia.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from evidentia.db.types import str_enum
from evidentia.modules.catalog.models import CardProduct


class MatchSource(StrEnum):
    CACHE = "cache"
    LOOKUP = "lookup"
    MANUAL = "manual"
    ADMIN = "admin"


class BinReviewReason(StrEnum):
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    MANUAL_CHOICE = "manual_choice"


class BinReviewStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class UserCard(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A card in a customer's wallet. Only the BIN is stored, never the full number."""

    __tablename__ = "user_cards"
    __table_args__ = (UniqueConstraint("user_id", "card_product_id", "bin"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    card_product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("card_products.id", ondelete="CASCADE")
    )
    bin: Mapped[str] = mapped_column(String(8))
    nickname: Mapped[str | None] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(default=True)
    match_source: Mapped[MatchSource] = mapped_column(str_enum(MatchSource))
    match_confidence: Mapped[float]

    card_product: Mapped[CardProduct] = relationship(lazy="joined")


class BinMapping(Base):
    """Cache of resolved BINs per country (layer 1 of matching)."""

    __tablename__ = "bin_mappings"

    bin: Mapped[str] = mapped_column(String(8), primary_key=True)
    country_code: Mapped[str] = mapped_column(String(2), primary_key=True)
    card_product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("card_products.id", ondelete="CASCADE")
    )
    confidence: Mapped[float]
    source: Mapped[MatchSource] = mapped_column(str_enum(MatchSource))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class BinReview(UUIDPrimaryKeyMixin, Base):
    """A BIN an administrator should map by hand."""

    __tablename__ = "bin_reviews"
    __table_args__ = (
        Index(
            "uq_bin_reviews_open",
            "bin",
            "country_code",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    bin: Mapped[str] = mapped_column(String(8))
    country_code: Mapped[str] = mapped_column(String(2))
    reason: Mapped[BinReviewReason] = mapped_column(str_enum(BinReviewReason))
    status: Mapped[BinReviewStatus] = mapped_column(
        str_enum(BinReviewStatus), default=BinReviewStatus.OPEN
    )
    candidates: Mapped[list[Any]] = mapped_column(default=list)
    lookup_response: Mapped[list[Any]] = mapped_column(default=list)
    resolved_card_product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("card_products.id", ondelete="SET NULL")
    )
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    resolved_at: Mapped[datetime | None]
