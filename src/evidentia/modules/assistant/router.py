from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from evidentia.ai.telemetry import LLMTelemetry
from evidentia.api.deps import AIGatewayDep, SessionDep, SettingsDep
from evidentia.core.metrics import prometheus_sink
from evidentia.modules.admin.store import prompt_registry
from evidentia.modules.assistant.engine import AnswerEngine
from evidentia.modules.assistant.retrieval import PgVectorRetriever
from evidentia.modules.assistant.schemas import AskRequest, AskResponse
from evidentia.modules.auth.dependencies import CurrentUser
from evidentia.modules.auth.models import User
from evidentia.modules.cards.scope import ScopedPolicy, resolve_policy_scope
from evidentia.modules.ops.service import save_calls


def get_answer_engine(
    session: SessionDep, settings: SettingsDep, shared: AIGatewayDep
) -> AnswerEngine:
    ai = shared.with_telemetry(LLMTelemetry([prometheus_sink]))

    async def scope(user: User, card_id: uuid.UUID | None) -> list[ScopedPolicy]:
        return await resolve_policy_scope(session, user.id, card_id=card_id)

    retriever = PgVectorRetriever(
        session,
        embedding_model=ai.embeddings.model,
        embedding_version=settings.llm.embedding_version,
    )
    return AnswerEngine(ai, prompt_registry(session), retriever, scope, settings.retrieval)


EngineDep = Annotated[AnswerEngine, Depends(get_answer_engine)]

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/ask", response_model=AskResponse, response_model_exclude_none=True)
async def ask(
    payload: AskRequest, user: CurrentUser, engine: EngineDep, session: SessionDep
) -> AskResponse:
    """Answers from the customer's own policies only; says so when facts are missing.

    Each question stands alone: no conversation history is stored, and only call
    metadata (never the question or answer) is persisted for cost accounting.
    """
    try:
        return await engine.ask(user, payload)
    finally:
        await save_calls(session, engine.gateway.telemetry.records)
