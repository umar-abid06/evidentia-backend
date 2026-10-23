from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from evidentia.ai.prompts import PromptRegistry
from evidentia.api.deps import AIGatewayDep, SessionDep, SettingsDep
from evidentia.modules.assistant.engine import AnswerEngine
from evidentia.modules.assistant.retrieval import PgVectorRetriever
from evidentia.modules.assistant.schemas import AskRequest, AskResponse
from evidentia.modules.auth.dependencies import CurrentUser
from evidentia.modules.auth.models import User
from evidentia.modules.cards.scope import ScopedPolicy, resolve_policy_scope


def get_answer_engine(session: SessionDep, settings: SettingsDep, ai: AIGatewayDep) -> AnswerEngine:
    async def scope(user: User, card_id: uuid.UUID | None) -> list[ScopedPolicy]:
        return await resolve_policy_scope(session, user.id, card_id=card_id)

    retriever = PgVectorRetriever(
        session,
        embedding_model=ai.embeddings.model,
        embedding_version=settings.llm.embedding_version,
    )
    return AnswerEngine(ai, PromptRegistry(), retriever, scope, settings.retrieval)


EngineDep = Annotated[AnswerEngine, Depends(get_answer_engine)]

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/ask", response_model=AskResponse, response_model_exclude_none=True)
async def ask(payload: AskRequest, user: CurrentUser, engine: EngineDep) -> AskResponse:
    """Answers from the customer's own policies only; says so when facts are missing."""
    return await engine.ask(user, payload)
