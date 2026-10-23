"""Versioning, review decisions and change history for published knowledge."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from evidentia.api.pagination import Page, PageParams, paginate
from evidentia.core.errors import ConflictError, NotFoundError
from evidentia.core.i18n import Language
from evidentia.core.tasks import TaskCall, enqueue, enqueue_after_commit
from evidentia.modules.audit.models import record_audit
from evidentia.modules.catalog.models import Policy
from evidentia.modules.documents.models import (
    Document,
    DocumentIntent,
    DocumentStatus,
    ReviewStatus,
)
from evidentia.modules.extraction.amendments import FieldChange, diff_records, reconcile
from evidentia.modules.extraction.schemas import CoverageRecord
from evidentia.modules.knowledge.chunker import record_hash
from evidentia.modules.knowledge.models import (
    CoverageTranslation,
    PolicyChange,
    PolicyVersion,
    VersionStatus,
)
from evidentia.modules.knowledge.schemas import ApprovalResult, CoverageView
from evidentia.modules.knowledge.translation import customer_view, view_hash
from evidentia.workers.names import PUBLISH_POLICY_VERSION, TRANSLATE_COVERAGE

REVIEWABLE = {ReviewStatus.PENDING, ReviewStatus.NEEDS_REVIEW}


class KnowledgeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def live_version(self, policy_id: uuid.UUID) -> PolicyVersion | None:
        result = await self.session.scalars(
            select(PolicyVersion).where(
                PolicyVersion.policy_id == policy_id, PolicyVersion.status == VersionStatus.READY
            )
        )
        return result.first()

    async def live_versions(self, policy_ids: list[uuid.UUID]) -> dict[uuid.UUID, PolicyVersion]:
        if not policy_ids:
            return {}
        result = await self.session.scalars(
            select(PolicyVersion).where(
                PolicyVersion.policy_id.in_(policy_ids),
                PolicyVersion.status == VersionStatus.READY,
            )
        )
        return {version.policy_id: version for version in result}

    async def list_versions(self, policy_id: uuid.UUID, params: PageParams) -> Page[Any]:
        query = (
            select(PolicyVersion)
            .where(PolicyVersion.policy_id == policy_id)
            .order_by(PolicyVersion.version_number.desc())
        )
        return await paginate(self.session, query, params)

    async def get_version(self, policy_id: uuid.UUID, version_id: uuid.UUID) -> PolicyVersion:
        version = await self.session.get(PolicyVersion, version_id)
        if version is None or version.policy_id != policy_id:
            raise NotFoundError("Policy version not found.", code="version_not_found")
        return version

    async def changes(self, policy_id: uuid.UUID, params: PageParams) -> Page[Any]:
        query = (
            select(PolicyChange)
            .where(PolicyChange.policy_id == policy_id)
            .order_by(PolicyChange.created_at.desc(), PolicyChange.field_path)
        )
        return await paginate(self.session, query, params)

    async def stage_version(
        self,
        policy_id: uuid.UUID,
        record: CoverageRecord,
        *,
        document_id: uuid.UUID | None,
        actor_id: uuid.UUID | None,
        changes: list[FieldChange] | None = None,
    ) -> PolicyVersion | None:
        """Creates a pending version, or returns None when content is unchanged."""
        content_hash = record_hash(record)
        live = await self.live_version(policy_id)
        if live is not None and live.content_hash == content_hash:
            return None  # zero-cost republish: nothing to chunk or embed

        latest = await self.session.scalar(
            select(func.max(PolicyVersion.version_number)).where(
                PolicyVersion.policy_id == policy_id
            )
        )
        version = PolicyVersion(
            policy_id=policy_id,
            version_number=(latest or 0) + 1,
            record=record.model_dump(mode="json"),
            content_hash=content_hash,
            source_document_id=document_id,
            approved_by_id=actor_id,
        )
        self.session.add(version)
        await self.session.flush()

        if changes is None:
            base = CoverageRecord.model_validate(live.record) if live else CoverageRecord()
            changes = diff_records(base, record)
        self.session.add_all(
            PolicyChange(
                policy_id=policy_id,
                policy_version_id=version.id,
                document_id=document_id,
                actor_id=actor_id,
                field_path=change.path,
                action=change.action,
                old_value=change.old,
                new_value=change.new,
            )
            for change in changes
        )
        enqueue_after_commit(self.session, PUBLISH_POLICY_VERSION, str(version.id))
        return version

    async def approve(self, document: Document, *, actor_id: uuid.UUID | None) -> ApprovalResult:
        """Approves an extraction and stages its version. Caller commits."""
        if document.status is not DocumentStatus.COMPLETED or document.extraction is None:
            raise ConflictError("Only processed documents can be approved.", code="not_processed")
        if document.review_status not in REVIEWABLE:
            raise ConflictError("Document was already reviewed.", code="already_reviewed")

        extracted = CoverageRecord.model_validate(document.extraction)
        changes: list[FieldChange] | None = None
        record = extracted
        if document.intent is DocumentIntent.AMENDMENT:
            live = await self.live_version(document.policy_id)
            if live is None:
                raise ConflictError("There is no live version to amend.", code="no_live_version")
            result = reconcile(CoverageRecord.model_validate(live.record), extracted)
            if not result.changes:
                raise ConflictError("The amendment changes nothing.", code="no_changes")
            record, changes = result.record, result.changes

        version = await self.stage_version(
            document.policy_id,
            record,
            document_id=document.id,
            actor_id=actor_id,
            changes=changes,
        )
        document.review_status = ReviewStatus.APPROVED
        document.reviewed_by_id = actor_id
        document.reviewed_at = datetime.now(UTC)
        record_audit(
            self.session,
            actor_id=actor_id,
            action="document.approved" if actor_id else "document.auto_approved",
            entity_type="document",
            entity_id=document.id,
            payload={"version_id": str(version.id) if version else None},
        )
        return ApprovalResult(
            document_id=document.id,
            version_id=version.id if version else None,
            unchanged=version is None,
        )

    async def approve_document(
        self, document_id: uuid.UUID, *, actor_id: uuid.UUID
    ) -> ApprovalResult:
        document = await self._document(document_id)
        result = await self.approve(document, actor_id=actor_id)
        await self.session.commit()
        return result

    async def reject_document(
        self, document_id: uuid.UUID, *, actor_id: uuid.UUID, reason: str
    ) -> Document:
        document = await self._document(document_id)
        if document.review_status not in REVIEWABLE:
            raise ConflictError("Document was already reviewed.", code="already_reviewed")
        document.review_status = ReviewStatus.REJECTED
        document.reviewed_by_id = actor_id
        document.reviewed_at = datetime.now(UTC)
        record_audit(
            self.session,
            actor_id=actor_id,
            action="document.rejected",
            entity_type="document",
            entity_id=document.id,
            payload={"reason": reason},
        )
        await self.session.commit()
        return document

    async def republish(self, policy_id: uuid.UUID, version_id: uuid.UUID) -> PolicyVersion:
        """Resumes a failed publish where it stopped."""
        version = await self.get_version(policy_id, version_id)
        if version.status is not VersionStatus.FAILED:
            raise ConflictError("Only failed versions can be resumed.", code="not_failed")
        version.status = VersionStatus.PENDING
        enqueue_after_commit(self.session, PUBLISH_POLICY_VERSION, str(version.id))
        await self.session.commit()
        return version

    async def coverage_view(self, policy: Policy, language: Language) -> CoverageView:
        """Customer-facing coverage; translations are generated once per language."""
        live = await self.live_version(policy.id)
        if live is None:
            raise NotFoundError("No published coverage yet.", code="coverage_not_published")
        view = customer_view(
            CoverageRecord.model_validate(live.record), policy_label=policy.display_name
        )
        if language == "en":
            return CoverageView(version_id=live.id, language="en", status="original", coverage=view)
        cached = await self.session.scalar(
            select(CoverageTranslation).where(
                CoverageTranslation.policy_version_id == live.id,
                CoverageTranslation.language == language,
            )
        )
        if cached is not None and cached.source_hash == view_hash(view):
            return CoverageView(
                version_id=live.id, language=language, status="translated", coverage=cached.payload
            )
        enqueue(TaskCall(TRANSLATE_COVERAGE, (str(live.id), language)))
        return CoverageView(version_id=live.id, language="en", status="pending", coverage=view)

    async def _document(self, document_id: uuid.UUID) -> Document:
        document = await self.session.get(Document, document_id)
        if document is None:
            raise NotFoundError("Document not found.", code="document_not_found")
        return document
