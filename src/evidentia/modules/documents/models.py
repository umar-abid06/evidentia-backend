from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from evidentia.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from evidentia.db.types import str_enum


class DocumentIntent(StrEnum):
    FULL_POLICY = "full_policy"
    AMENDMENT = "amendment"


class DocumentStatus(StrEnum):
    UPLOADING = "uploading"
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"


QUEUED_STATUSES = (DocumentStatus.PENDING, DocumentStatus.PROCESSING)


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        # Fingerprint guard: one live upload per file content.
        Index(
            "uq_documents_active_sha256",
            "sha256",
            unique=True,
            postgresql_where=text("status <> 'failed' AND review_status <> 'rejected'"),
        ),
        Index("ix_documents_status", "status"),
        Index("ix_documents_review_status", "review_status"),
    )

    policy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("policies.id", ondelete="CASCADE"), index=True
    )
    intent: Mapped[DocumentIntent] = mapped_column(str_enum(DocumentIntent))
    status: Mapped[DocumentStatus] = mapped_column(
        str_enum(DocumentStatus), default=DocumentStatus.UPLOADING
    )
    review_status: Mapped[ReviewStatus] = mapped_column(
        str_enum(ReviewStatus), default=ReviewStatus.PENDING
    )
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)

    page_count: Mapped[int | None]
    needs_ocr: Mapped[bool] = mapped_column(default=False)
    extraction_model: Mapped[str | None] = mapped_column(String(100))
    extraction: Mapped[dict[str, Any] | None]
    overall_confidence: Mapped[float | None]
    review_reasons: Mapped[list[Any]] = mapped_column(default=list)
    conflicts: Mapped[list[Any]] = mapped_column(default=list)
    error: Mapped[dict[str, Any] | None]
    report: Mapped[dict[str, Any] | None]
    attempts: Mapped[int] = mapped_column(default=0)

    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None]
    processing_started_at: Mapped[datetime | None]
    processed_at: Mapped[datetime | None]

    pages: Mapped[list[DocumentPage]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentPage.page_number",
    )


class DocumentPage(UUIDPrimaryKeyMixin, Base):
    """Page-level text: the anchor every extracted fact cites."""

    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number"),)

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    page_number: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int]

    document: Mapped[Document] = relationship(back_populates="pages")
