from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from evidentia.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Policy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A coverage programme. Its knowledge lives in versioned records."""

    __tablename__ = "policies"

    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200))
    insurer_name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(default=True)

    card_products: Mapped[list[CardProduct]] = relationship(back_populates="policy")


class CardProduct(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A card a customer can hold, and the policy that comes with it."""

    __tablename__ = "card_products"
    __table_args__ = (
        UniqueConstraint("issuer_name", "product_name", "country_code"),
        Index("ix_card_products_country_active", "country_code", "is_active"),
    )

    issuer_name: Mapped[str] = mapped_column(String(200))
    product_name: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200))
    scheme: Mapped[str | None] = mapped_column(String(40))
    funding_type: Mapped[str] = mapped_column(String(20), default="credit")
    country_code: Mapped[str] = mapped_column(String(2))
    is_active: Mapped[bool] = mapped_column(default=True)
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policies.id", ondelete="SET NULL"), index=True
    )

    policy: Mapped[Policy | None] = relationship(back_populates="card_products")
