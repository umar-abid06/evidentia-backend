"""Reusable column types."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum


def str_enum[E: StrEnum](enum_cls: type[E], *, length: int = 32) -> Enum:
    """Stores a StrEnum as VARCHAR + CHECK, so adding values needs no ALTER TYPE."""
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=True,
        length=length,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
        name=f"{enum_cls.__name__.lower()}_enum",
    )
