from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from evidentia.core.config import Settings
from evidentia.core.security import create_token
from evidentia.modules.documents.models import (
    Document,
    DocumentIntent,
    DocumentStatus,
    ReviewStatus,
)
from evidentia.modules.knowledge.models import PolicyVersion, VersionStatus
from evidentia.modules.notifications.events import for_document, for_version
from evidentia.modules.notifications.hub import NotificationHub


def _document(status: DocumentStatus, review: ReviewStatus, **extra: object) -> Document:
    return Document(
        id=uuid.uuid4(),
        policy_id=uuid.uuid4(),
        intent=DocumentIntent.FULL_POLICY,
        status=status,
        review_status=review,
        original_filename="wording.pdf",
        content_type="application/pdf",
        size_bytes=10,
        sha256="a" * 64,
        storage_key="k",
        review_reasons=extra.get("review_reasons", []),
        error=extra.get("error"),
    )


def test_document_events_are_colour_coded() -> None:
    failed = for_document(
        _document(DocumentStatus.FAILED, ReviewStatus.NEEDS_REVIEW, error={"message": "boom"})
    )
    review = for_document(
        _document(
            DocumentStatus.COMPLETED,
            ReviewStatus.NEEDS_REVIEW,
            review_reasons=[{"code": "low_confidence"}],
        )
    )
    clean = for_document(_document(DocumentStatus.COMPLETED, ReviewStatus.APPROVED))
    assert (failed.level, failed.message) == ("error", "boom")
    assert review.level == "warning"
    assert "low_confidence" in review.message
    assert clean.level == "success"


def test_version_events() -> None:
    ready = PolicyVersion(
        id=uuid.uuid4(),
        version_number=3,
        status=VersionStatus.READY,
        chunk_count=12,
        embedding_calls=1,
    )
    assert for_version(ready).title == "Version 3 is live"


class _Socket:
    def __init__(self, *, broken: bool = False) -> None:
        self.broken = broken
        self.received: list[str] = []

    async def send_text(self, data: str) -> None:
        if self.broken:
            raise RuntimeError("gone")
        self.received.append(data)


async def test_hub_fans_out_and_drops_dead_sockets() -> None:
    hub = NotificationHub(redis=None)  # type: ignore[arg-type]
    alive, dead = _Socket(), _Socket(broken=True)
    hub.connect(alive)
    hub.connect(dead)
    await hub.broadcast('{"level": "success"}')
    assert alive.received == ['{"level": "success"}']
    assert hub.clients == {alive}


@pytest.fixture
def ws_app(app: FastAPI) -> FastAPI:
    app.state.notifications = NotificationHub(redis=None)  # type: ignore[arg-type]
    return app


PATH = "/api/v1/ws/admin/notifications"


def test_websocket_rejects_customers_and_bad_tokens(ws_app: FastAPI, settings: Settings) -> None:
    client = TestClient(ws_app)
    customer = create_token(uuid.uuid4(), "customer", "access", settings.auth)
    for token in ("not-a-token", customer):
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(f"{PATH}?token={token}"):
            pass


def test_websocket_registers_staff(ws_app: FastAPI, settings: Settings) -> None:
    client = TestClient(ws_app)
    token = create_token(uuid.uuid4(), "reviewer", "access", settings.auth)
    hub: NotificationHub = ws_app.state.notifications
    with client.websocket_connect(f"{PATH}?token={token}") as socket:
        socket.send_text("ping")
        assert len(hub.clients) == 1
    assert hub.clients == set()
