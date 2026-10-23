from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from evidentia.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from evidentia.db.types import str_enum

EMBEDDING_DIMENSIONS = 1536


class VersionStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    ARCHIVED = "archived"


class ChunkType(StrEnum):
    COVERAGE = "coverage"
    OVERVIEW = "overview"


class PolicyVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An approved coverage record. Searchable only once every chunk has a vector."""

    __tablename__ = "policy_versions"
    __table_args__ = (
        UniqueConstraint("policy_id", "version_number"),
        Index(
            "uq_policy_versions_one_ready",
            "policy_id",
            unique=True,
            postgresql_where=text("status = 'ready'"),
        ),
    )

    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policies.id", ondelete="CASCADE"))
    version_number: Mapped[int]
    status: Mapped[VersionStatus] = mapped_column(
        str_enum(VersionStatus), default=VersionStatus.PENDING
    )
    record: Mapped[dict[str, Any]]
    content_hash: Mapped[str] = mapped_column(String(64))
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    published_at: Mapped[datetime | None]
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embedding_version: Mapped[str | None] = mapped_column(String(20))
    chunk_count: Mapped[int] = mapped_column(default=0)
    token_count: Mapped[int] = mapped_column(default=0)
    embedding_calls: Mapped[int] = mapped_column(default=0)
    publish_duration_ms: Mapped[int | None]
    error: Mapped[dict[str, Any] | None]

    chunks: Mapped[list[KnowledgeChunk]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="KnowledgeChunk.ordinal"
    )


class KnowledgeChunk(UUIDPrimaryKeyMixin, Base):
    """One self-contained fact (a coverage item or an overview section) and its vector."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("policy_version_id", "chunk_key"),
        Index("ix_knowledge_chunks_version_category", "policy_version_id", "category"),
        Index(
            "ix_knowledge_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    policy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="CASCADE")
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(index=True)
    chunk_key: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int]
    chunk_type: Mapped[ChunkType] = mapped_column(str_enum(ChunkType))
    category: Mapped[str] = mapped_column(String(60))
    domain: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    fields: Mapped[dict[str, Any]] = mapped_column(default=dict)
    customer_facing: Mapped[bool] = mapped_column(default=True)
    token_count: Mapped[int]
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embedding_version: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    version: Mapped[PolicyVersion] = relationship(back_populates="chunks")


class CoverageTranslation(UUIDPrimaryKeyMixin, Base):
    """Customer-facing coverage in one language, cached until the version changes."""

    __tablename__ = "coverage_translations"
    __table_args__ = (UniqueConstraint("policy_version_id", "language"),)

    policy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="CASCADE")
    )
    language: Mapped[str] = mapped_column(String(5))
    payload: Mapped[dict[str, Any]]
    source_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PolicyChange(UUIDPrimaryKeyMixin, Base):
    """Field-level change history (path, action, old value, new value)."""

    __tablename__ = "policy_changes"
    __table_args__ = (Index("ix_policy_changes_policy_created", "policy_id", "created_at"),)

    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policies.id", ondelete="CASCADE"))
    policy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="CASCADE")
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    field_path: Mapped[str] = mapped_column(String(300))
    action: Mapped[str] = mapped_column(String(20))
    old_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
