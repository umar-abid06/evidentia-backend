from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from evidentia.core.tasks import RecordingDispatcher, enqueue_after_commit


def _session() -> Session:
    session = Session(create_engine("sqlite://"))
    session.execute(text("SELECT 1"))
    return session


def test_jobs_are_sent_only_after_commit(dispatcher: RecordingDispatcher) -> None:
    session = _session()
    enqueue_after_commit(session, "evidentia.documents.process", "doc-1")
    assert dispatcher.calls == []
    session.commit()
    assert dispatcher.names() == ["evidentia.documents.process"]
    assert dispatcher.calls[0].args == ("doc-1",)


def test_rolled_back_jobs_are_dropped(dispatcher: RecordingDispatcher) -> None:
    session = _session()
    enqueue_after_commit(session, "evidentia.documents.process", "doc-1")
    session.rollback()
    session.execute(text("SELECT 1"))
    session.commit()
    assert dispatcher.calls == []
