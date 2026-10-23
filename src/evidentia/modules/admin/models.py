from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from evidentia.db.base import Base, UUIDPrimaryKeyMixin


class SystemSetting(Base):
    """Operator-managed configuration. Secret values are stored Fernet-encrypted."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(default=False)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class PromptOverride(UUIDPrimaryKeyMixin, Base):
    """Versioned instruction text; at most one active override per prompt key."""

    __tablename__ = "prompt_overrides"
    __table_args__ = (
        Index(
            "uq_prompt_overrides_active",
            "key",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    key: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
