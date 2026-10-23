from __future__ import annotations

import uuid

from evidentia.integrations.storage import build_storage
from evidentia.modules.documents.processing import DocumentProcessor
from evidentia.modules.documents.text import PdfTextExtractor
from evidentia.workers.celery_app import celery_app
from evidentia.workers.names import PROCESS_DOCUMENT
from evidentia.workers.runtime import WorkerContext, run_job


async def _process(ctx: WorkerContext, document_id: uuid.UUID) -> None:
    async with ctx.db.session() as session:
        processor = DocumentProcessor(
            session, build_storage(ctx.settings.storage), PdfTextExtractor()
        )
        await processor.run(document_id)


@celery_app.task(name=PROCESS_DOCUMENT, acks_late=True)
def process_document(document_id: str) -> None:
    """Failures are recorded on the document itself; the task never re-raises them."""
    run_job(lambda ctx: _process(ctx, uuid.UUID(document_id)), document_id=document_id)
