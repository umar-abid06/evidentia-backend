from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from evidentia.db.registry import load_models

ROOT = Path(__file__).resolve().parents[1]


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migrations_create_every_model_table(capsys: pytest.CaptureFixture[str]) -> None:
    """Drift guard: the migration chain must build exactly what the models declare."""
    command.upgrade(alembic_config("postgresql+asyncpg://u:p@localhost/db"), "head", sql=True)
    sql = capsys.readouterr().out
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)" in sql
    for table in load_models().tables:
        assert f"CREATE TABLE {table} (" in sql, table
