from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from evidentia.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from evidentia.db.types import str_enum


class UserRole(StrEnum):
    CUSTOMER = "customer"
    REVIEWER = "reviewer"
    ADMIN = "admin"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(str_enum(UserRole), default=UserRole.CUSTOMER)
    country_code: Mapped[str | None] = mapped_column(String(2))
    preferred_language: Mapped[str] = mapped_column(String(5), default="en")
    is_active: Mapped[bool] = mapped_column(default=True)
    last_login_at: Mapped[datetime | None]

    @property
    def is_staff(self) -> bool:
        return self.role in {UserRole.REVIEWER, UserRole.ADMIN}
