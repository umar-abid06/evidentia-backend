from __future__ import annotations

import hashlib
import uuid

import pytest
from httpx import AsyncClient

from evidentia.core.tasks import RecordingDispatcher
from evidentia.db.session import Database
from evidentia.integrations.storage import InMemoryStorage
from evidentia.modules.documents.models import Document, DocumentStatus, ReviewStatus
from evidentia.modules.documents.processing import DocumentProcessor
from evidentia.modules.documents.text import PdfTextExtractor
from evidentia.workers.names import PROCESS_DOCUMENT
from tests.factories import make_pdf

pytestmark = pytest.mark.integration


async def _policy_with_card(api: AsyncClient, headers: dict[str, str]) -> str:
    policy = await api.post(
        "/api/v1/admin/policies",
        json={
            "code": "silver-travel",
            "name": "Silver Travel",
            "display_name": "Silver Travel Cover",
            "insurer_name": "Example Insurance SE",
        },
        headers=headers,
    )
    policy_id: str = policy.json()["id"]
    await api.post(
        "/api/v1/admin/card-products",
        json={
            "issuer_name": "Example Bank",
            "product_name": "Silver Mastercard",
            "display_name": "Example Silver",
            "country_code": "DE",
            "policy_id": policy_id,
        },
        headers=headers,
    )
    return policy_id


async def _upload(
    api: AsyncClient,
    headers: dict[str, str],
    storage: InMemoryStorage,
    policy_id: str,
    pdf: bytes,
) -> str:
    ticket = await api.post(
        "/api/v1/admin/documents/uploads",
        json={
            "policy_id": policy_id,
            "intent": "full_policy",
            "filename": "wording.pdf",
            "size_bytes": len(pdf),
            "sha256": hashlib.sha256(pdf).hexdigest(),
        },
        headers=headers,
    )
    assert ticket.status_code == 201, ticket.text
    body = ticket.json()
    storage.put(body["upload"]["fields"]["key"], pdf)
    return str(body["document_id"])


async def test_upload_is_fingerprinted_queued_and_read_page_by_page(
    api: AsyncClient,
    admin_headers: dict[str, str],
    storage: InMemoryStorage,
    dispatcher: RecordingDispatcher,
    db: Database,
) -> None:
    policy_id = await _policy_with_card(api, admin_headers)
    pdf = make_pdf(["Emergency medical expenses EUR 1,000,000", "Baggage delay EUR 300"])
    document_id = await _upload(api, admin_headers, storage, policy_id, pdf)

    duplicate = await api.post(
        "/api/v1/admin/documents/uploads",
        json={
            "policy_id": policy_id,
            "intent": "full_policy",
            "filename": "copy.pdf",
            "size_bytes": len(pdf),
            "sha256": hashlib.sha256(pdf).hexdigest(),
        },
        headers=admin_headers,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["document_id"] == document_id

    completed = await api.post(
        f"/api/v1/admin/documents/{document_id}/complete", headers=admin_headers
    )
    assert completed.status_code == 202
    assert completed.json()["status"] == "pending"
    assert dispatcher.names() == [PROCESS_DOCUMENT]

    async with db.session() as session:
        await DocumentProcessor(session, storage, PdfTextExtractor()).run(uuid.UUID(document_id))

    page = await api.get(f"/api/v1/admin/documents/{document_id}/pages/2", headers=admin_headers)
    assert "Baggage delay" in page.json()["text"]
    detail = await api.get(f"/api/v1/admin/documents/{document_id}", headers=admin_headers)
    assert detail.json()["page_count"] == 2


async def test_scans_are_flagged_for_ocr(
    api: AsyncClient, admin_headers: dict[str, str], storage: InMemoryStorage, db: Database
) -> None:
    policy_id = await _policy_with_card(api, admin_headers)
    document_id = await _upload(api, admin_headers, storage, policy_id, make_pdf(["", ""]))
    await api.post(f"/api/v1/admin/documents/{document_id}/complete", headers=admin_headers)

    async with db.session() as session:
        await DocumentProcessor(session, storage, PdfTextExtractor()).run(uuid.UUID(document_id))
        document = await session.get(Document, uuid.UUID(document_id))
        assert document is not None
        assert document.status is DocumentStatus.FAILED
        assert document.review_status is ReviewStatus.NEEDS_REVIEW
        assert document.needs_ocr
        assert document.error is not None
        assert document.error["code"] == "scanned_document"
