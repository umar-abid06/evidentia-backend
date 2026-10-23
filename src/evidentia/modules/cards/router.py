from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status

from evidentia.api.deps import SessionDep
from evidentia.api.pagination import Page, PageDep
from evidentia.core.i18n import Language, as_language
from evidentia.integrations.bin_lookup import BinLookup
from evidentia.modules.auth.dependencies import AdminUser, CurrentUser
from evidentia.modules.cards.models import BinReview, BinReviewStatus, UserCard
from evidentia.modules.cards.schemas import (
    AddCardRequest,
    BinReviewRead,
    CardRead,
    IdentifyRequest,
    IdentifyResult,
    ResolveBinRequest,
)
from evidentia.modules.cards.service import CardService
from evidentia.modules.knowledge.schemas import CoverageView


def get_bin_lookup(request: Request) -> BinLookup:
    lookup: BinLookup = request.app.state.bin_lookup
    return lookup


def get_card_service(
    session: SessionDep, lookup: Annotated[BinLookup, Depends(get_bin_lookup)]
) -> CardService:
    return CardService(session, lookup)


CardsDep = Annotated[CardService, Depends(get_card_service)]


def _read(card: UserCard) -> CardRead:
    product = card.card_product
    return CardRead(
        id=card.id,
        bin=card.bin,
        nickname=card.nickname,
        card_product_id=card.card_product_id,
        display_name=product.display_name,
        issuer_name=product.issuer_name,
        policy_id=product.policy_id,
        match_source=card.match_source,
        match_confidence=card.match_confidence,
        created_at=card.created_at,
    )


router = APIRouter(prefix="/cards", tags=["cards"])
admin_router = APIRouter(prefix="/admin/bin-reviews", tags=["admin: cards"])


@router.post("/identify", response_model=IdentifyResult)
async def identify(
    payload: IdentifyRequest, user: CurrentUser, service: CardsDep
) -> IdentifyResult:
    """Finds the coverage that comes with a card from its first digits."""
    return await service.identify(user, payload.bin, payload.country_code)


@router.post("", response_model=CardRead, status_code=status.HTTP_201_CREATED)
async def add_card(payload: AddCardRequest, user: CurrentUser, service: CardsDep) -> CardRead:
    return _read(await service.add_card(user, payload))


@router.get("", response_model=list[CardRead])
async def list_cards(user: CurrentUser, service: CardsDep) -> list[CardRead]:
    return [_read(card) for card in await service.list_cards(user)]


@router.delete("/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_card(card_id: uuid.UUID, user: CurrentUser, service: CardsDep) -> None:
    await service.remove_card(user, card_id)


@router.get("/{card_id}/coverage", response_model=CoverageView)
async def card_coverage(
    card_id: uuid.UUID,
    user: CurrentUser,
    service: CardsDep,
    language: Annotated[Language | None, Query()] = None,
) -> CoverageView:
    """The card's coverage in the customer's language (translated once, then cached)."""
    return await service.coverage(user, card_id, language or as_language(user.preferred_language))


@admin_router.get("", response_model=Page[BinReviewRead])
async def list_bin_reviews(
    _: AdminUser,
    service: CardsDep,
    page: PageDep,
    review_status: Annotated[BinReviewStatus, Query(alias="status")] = BinReviewStatus.OPEN,
) -> Page[Any]:
    return await service.list_reviews(page, review_status)


@admin_router.post("/{review_id}/resolve", response_model=BinReviewRead)
async def resolve_bin_review(
    review_id: uuid.UUID, payload: ResolveBinRequest, user: AdminUser, service: CardsDep
) -> BinReview:
    return await service.resolve_review(review_id, payload.card_product_id, actor_id=user.id)


@admin_router.post("/{review_id}/dismiss", response_model=BinReviewRead)
async def dismiss_bin_review(review_id: uuid.UUID, user: AdminUser, service: CardsDep) -> BinReview:
    return await service.dismiss_review(review_id, actor_id=user.id)
