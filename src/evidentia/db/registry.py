"""Imports every ORM module so ``Base.metadata`` is complete (Alembic, tests)."""

from __future__ import annotations

import importlib

from sqlalchemy import MetaData

from evidentia.db.base import Base

MODEL_MODULES: tuple[str, ...] = ("evidentia.modules.auth.models",)


def load_models() -> MetaData:
    for module in MODEL_MODULES:
        importlib.import_module(module)
    return Base.metadata
