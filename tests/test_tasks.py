from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from evidentia.core.tasks import RecordingDispatcher, enqueue_after_commit


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    with Session(engine) as db:
        db.execute(text("SELECT 1"))
        yield db
    engine.dispose()


def test_jobs_are_sent_only_after_commit(session: Session, dispatcher: RecordingDispatcher) -> None:
    enqueue_after_commit(session, "evidentia.documents.process", "doc-1")
    assert dispatcher.calls == []
    session.commit()
    assert dispatcher.names() == ["evidentia.documents.process"]
    assert dispatcher.calls[0].args == ("doc-1",)


def test_rolled_back_jobs_are_dropped(session: Session, dispatcher: RecordingDispatcher) -> None:
    enqueue_after_commit(session, "evidentia.documents.process", "doc-1")
    session.rollback()
    session.execute(text("SELECT 1"))
    session.commit()
    assert dispatcher.calls == []
