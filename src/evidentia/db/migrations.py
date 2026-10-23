"""Alembic autogenerate hooks shared by ``migrations/env.py``."""

from __future__ import annotations

from typing import Any

from pgvector.sqlalchemy import VECTOR


def render_item(type_: str, obj: Any, autogen_context: Any) -> str | bool:
    """Renders pgvector columns as importable code in generated migrations."""
    if type_ == "type" and isinstance(obj, VECTOR):
        autogen_context.imports.add("import pgvector.sqlalchemy")
        return f"pgvector.sqlalchemy.Vector({obj.dim})"
    return False
