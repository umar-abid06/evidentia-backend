from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from evidentia.modules.auth.dependencies import get_current_user
from evidentia.modules.auth.models import User, UserRole
from evidentia.modules.documents.text import (
    PdfTextExtractor,
    UnreadableDocumentError,
    is_scanned,
    normalize_page_text,
)
from tests.factories import make_pdf


def test_text_is_normalized_and_storage_safe() -> None:
    raw = "Medical\u00a0 expenses\t\tEUR 1,000,000\r\n\r\n\r\n\r\nPage\x00 footer  "
    assert normalize_page_text(raw) == "Medical expenses EUR 1,000,000\n\nPage footer"


def test_pdf_text_is_extracted_page_by_page() -> None:
    pdf = make_pdf(["Section 1\nEmergency medical expenses", "", "Baggage delay (6 hours)"])
    pages = PdfTextExtractor().extract(pdf)
    assert [page.number for page in pages] == [1, 2, 3]
    assert "Emergency medical expenses" in pages[0].text
    assert pages[1].is_blank
    assert "Baggage delay (6 hours)" in pages[2].text
    assert not is_scanned(pages)


def test_image_only_documents_are_detected_as_scans() -> None:
    pages = PdfTextExtractor().extract(make_pdf(["", ""]))
    assert is_scanned(pages)


def test_garbage_bytes_are_unreadable() -> None:
    with pytest.raises(UnreadableDocumentError):
        PdfTextExtractor().extract(b"definitely not a pdf")


def _admin() -> User:
    return User(
        id=uuid.uuid4(),
        email="ops@example.com",
        password_hash="x",
        role=UserRole.ADMIN,
        created_at=datetime.now(UTC),
    )


async def test_oversized_uploads_are_refused_before_any_work(
    app: FastAPI, client: AsyncClient
) -> None:
    app.dependency_overrides[get_current_user] = _admin
    response = await client.post(
        "/api/v1/admin/documents/uploads",
        json={
            "policy_id": str(uuid.uuid4()),
            "intent": "full_policy",
            "filename": "wording.pdf",
            "size_bytes": 500 * 1024 * 1024,
            "sha256": "a" * 64,
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "file_too_large"


async def test_upload_requests_validate_the_fingerprint(app: FastAPI, client: AsyncClient) -> None:
    app.dependency_overrides[get_current_user] = _admin
    response = await client.post(
        "/api/v1/admin/documents/uploads",
        json={
            "policy_id": str(uuid.uuid4()),
            "intent": "amendment",
            "filename": "endorsement.pdf",
            "size_bytes": 1024,
            "sha256": "not-a-hash",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
