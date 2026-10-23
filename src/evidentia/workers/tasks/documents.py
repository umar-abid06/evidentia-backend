from __future__ import annotations

import uuid

from evidentia.ai.factory import build_gateway
from evidentia.ai.prompts import PromptRegistry
from evidentia.integrations.storage import build_storage
from evidentia.modules.documents.processing import DocumentProcessor
from evidentia.modules.documents.text import PdfTextExtractor
from evidentia.modules.extraction.pipeline import ExtractionPipeline
from evidentia.modules.knowledge.auto_publish import AutoPublisher
from evidentia.modules.notifications.events import for_document
from evidentia.workers.celery_app import celery_app
from evidentia.workers.names import PROCESS_DOCUMENT
from evidentia.workers.runtime import WorkerContext, notifier, run_job


async def _process(ctx: WorkerContext, document_id: uuid.UUID) -> None:
    settings = ctx.settings
    async with ctx.db.session() as session:
        pipeline = ExtractionPipeline(
            build_gateway(settings.llm),
            PromptRegistry(),
            chunk_chars=settings.ingestion.reading_chunk_chars,
            parallelism=settings.llm.max_parallel_calls,
            confidence_threshold=settings.ingestion.review_confidence_threshold,
        )
        processor = DocumentProcessor(
            session,
            build_storage(settings.storage),
            PdfTextExtractor(),
            pipeline,
            after_extraction=AutoPublisher(session),
        )
        document = await processor.run(document_id)
    if document is not None:
        async with notifier(settings) as notify:
            await notify.publish(for_document(document))


@celery_app.task(name=PROCESS_DOCUMENT, acks_late=True)
def process_document(document_id: str) -> None:
    """Failures are recorded on the document itself; the task never re-raises them."""
    run_job(lambda ctx: _process(ctx, uuid.UUID(document_id)), document_id=document_id)
