"""Background job dispatch, decoupled from the queue implementation.

Jobs that read fresh rows are queued with :func:`enqueue_after_commit`, so a
worker can never pick up a record whose transaction was rolled back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

_PENDING_KEY = "evidentia.pending_tasks"

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TaskCall:
    name: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    countdown: float | None = None


class TaskDispatcher(Protocol):
    def send(self, call: TaskCall) -> None: ...


class CeleryDispatcher:
    """Sends by task name, so the API never imports worker modules."""

    def send(self, call: TaskCall) -> None:
        from evidentia.workers.celery_app import celery_app  # noqa: PLC0415 - lazy broker

        celery_app.send_task(
            call.name, args=list(call.args), kwargs=call.kwargs, countdown=call.countdown
        )


class RecordingDispatcher:
    """Collects calls instead of sending them (tests, eager local runs)."""

    def __init__(self) -> None:
        self.calls: list[TaskCall] = []

    def send(self, call: TaskCall) -> None:
        self.calls.append(call)

    def names(self) -> list[str]:
        return [call.name for call in self.calls]


_dispatcher: TaskDispatcher | None = None


def set_dispatcher(dispatcher: TaskDispatcher | None) -> None:
    global _dispatcher  # noqa: PLW0603 - process-wide wiring point
    _dispatcher = dispatcher


def get_dispatcher() -> TaskDispatcher:
    global _dispatcher  # noqa: PLW0603
    if _dispatcher is None:
        _dispatcher = CeleryDispatcher()
    return _dispatcher


def enqueue(call: TaskCall) -> None:
    get_dispatcher().send(call)
    log.info("task_enqueued", task=call.name)


def enqueue_after_commit(
    session: AsyncSession | Session,
    name: str,
    *args: Any,
    countdown: float | None = None,
    **kwargs: Any,
) -> None:
    sync = session.sync_session if isinstance(session, AsyncSession) else session
    sync.info.setdefault(_PENDING_KEY, []).append(TaskCall(name, args, kwargs, countdown))


@event.listens_for(Session, "after_commit")
def _send_pending(session: Session) -> None:
    for call in session.info.pop(_PENDING_KEY, []):
        try:
            enqueue(call)
        except Exception:
            # The data is committed; a recovery sweep can re-queue the job.
            log.exception("task_enqueue_failed", task=call.name)


@event.listens_for(Session, "after_soft_rollback")
def _drop_pending(session: Session, previous_transaction: SessionTransaction) -> None:
    if not previous_transaction.nested:
        session.info.pop(_PENDING_KEY, None)
