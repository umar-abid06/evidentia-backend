from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from evidentia.db.base import Base, UUIDPrimaryKeyMixin


class LLMCall(UUIDPrimaryKeyMixin, Base):
    """One model call: the unit of cost and latency accounting. Never stores content."""

    __tablename__ = "llm_calls"
    __table_args__ = (
        Index("ix_llm_calls_stage_created", "stage", "created_at"),
        Index("ix_llm_calls_document", "document_id"),
    )

    stage: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(100))
    prompt_tokens: Mapped[int]
    completion_tokens: Mapped[int]
    latency_ms: Mapped[float]
    payload_bytes: Mapped[int]
    retries: Mapped[int]
    success: Mapped[bool]
    error_type: Mapped[str | None] = mapped_column(String(80))
    document_id: Mapped[uuid.UUID | None]
    policy_version_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
