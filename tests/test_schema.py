from __future__ import annotations

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateIndex, CreateTable

from evidentia.db.registry import load_models


def test_every_model_compiles_to_postgres_ddl() -> None:
    metadata = load_models()
    configure_mappers()
    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    assert metadata.sorted_tables
    for table in metadata.sorted_tables:
        assert "CREATE TABLE" in str(CreateTable(table).compile(dialect=dialect))
        for index in table.indexes:
            assert "INDEX" in str(CreateIndex(index).compile(dialect=dialect))
