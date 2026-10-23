"""Card identification (six layers), the customer wallet and policy scoping."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from evidentia.api.pagination import Page, PageParams, paginate
from evidentia.core.errors import ConflictError, NotFoundError, UnprocessableError
from evidentia.core.i18n import Language
from evidentia.integrations.bin_lookup import BinLookup, BinLookupUnavailableError, BinRecord
from evidentia.modules.auth.models import User
from evidentia.modules.cards.matching import (
    AcceptanceRules,
    Scored,
    decide,
    issuer_score,
    product_score,
)
from evidentia.modules.cards.models import (
    BinMapping,
    BinReview,
    BinReviewReason,
    BinReviewStatus,
    MatchSource,
    UserCard,
)
from evidentia.modules.cards.schemas import AddCardRequest, CandidateCard, IdentifyResult
from evidentia.modules.cards.scope import ScopedPolicy, resolve_policy_scope
from evidentia.modules.catalog.models import CardProduct, Policy
from evidentia.modules.knowledge.schemas import CoverageView
from evidentia.modules.knowledge.service import KnowledgeService


def _candidate(product: CardProduct, confidence: float) -> CandidateCard:
    return CandidateCard(
        card_product_id=product.id,
        display_name=product.display_name,
        issuer_name=product.issuer_name,
        confidence=confidence,
    )


class CardService:
    def __init__(
        self, session: AsyncSession, lookup: BinLookup, rules: AcceptanceRules | None = None
    ) -> None:
        self.session = session
        self.lookup = lookup
        self.rules = rules or AcceptanceRules()

    async def identify(self, user: User, bin_number: str, country: str | None) -> IdentifyResult:
        country_code = self._country(user, country)

        # 1. Instant cache.
        cached = await self.session.get(BinMapping, (bin_number, country_code))
        if cached is not None:
            product = await self.session.get(CardProduct, cached.card_product_id)
            if product is not None and product.is_active:
                return IdentifyResult(
                    outcome="matched", match=_candidate(product, 100.0), source=MatchSource.CACHE
                )

        # 2. Issuer lookup. A busy service is reported as such: nothing cached or guessed.
        try:
            records = await self.lookup.lookup(bin_number)
        except BinLookupUnavailableError:
            return IdentifyResult(outcome="unavailable")

        # 3. Hard filters: credit-funded cards issued in the customer's country.
        eligible = [
            record
            for record in records
            if (record.funding_type or "").lower() == "credit"
            and record.country_code == country_code
        ]
        catalog = list(
            await self.session.scalars(
                select(CardProduct).where(
                    CardProduct.country_code == country_code, CardProduct.is_active.is_(True)
                )
            )
        )

        # 4-5. Fuzzy issuer and product match; keep each product's best record.
        best: dict[str, Scored] = {}
        for record in eligible:
            for product in catalog:
                scored = Scored(
                    product_id=str(product.id),
                    issuer=issuer_score(record.issuer, product.issuer_name),
                    product=product_score(record.product_name, product.product_name),
                )
                current = best.get(scored.product_id)
                if current is None or scored.average > current.average:
                    best[scored.product_id] = scored

        # 6. Acceptance rules.
        decision = decide(list(best.values()), self.rules)
        by_id = {str(product.id): product for product in catalog}
        if decision.outcome == "matched" and decision.winner is not None:
            product = by_id[decision.winner.product_id]
            await self._cache(bin_number, country_code, product.id, decision.winner.average)
            await self.session.commit()
            return IdentifyResult(
                outcome="matched",
                match=_candidate(product, decision.winner.average),
                source=MatchSource.LOOKUP,
            )

        candidates = [_candidate(by_id[s.product_id], s.average) for s in decision.ranked[:5]]
        reason = (
            BinReviewReason.AMBIGUOUS
            if decision.outcome == "ambiguous"
            else BinReviewReason.NOT_FOUND
        )
        await self._queue_review(bin_number, country_code, reason, candidates, records)
        await self.session.commit()
        return IdentifyResult(
            outcome=decision.outcome,
            candidates=candidates if decision.outcome == "ambiguous" else [],
        )

    async def add_card(self, user: User, request: AddCardRequest) -> UserCard:
        country_code = self._country(user, request.country_code)
        if request.card_product_id is not None:
            product = await self.session.get(CardProduct, request.card_product_id)
            if product is None or not product.is_active or product.country_code != country_code:
                raise UnprocessableError("That card is not available.", code="card_unavailable")
            source, confidence = MatchSource.MANUAL, 100.0
            if await self.session.get(BinMapping, (request.bin, country_code)) is None:
                await self._queue_review(
                    request.bin,
                    country_code,
                    BinReviewReason.MANUAL_CHOICE,
                    [_candidate(product, 100.0)],
                    [],
                )
        else:
            result = await self.identify(user, request.bin, country_code)
            if result.outcome != "matched" or result.match is None or result.source is None:
                raise ConflictError(
                    "The card could not be identified automatically.",
                    code=f"card_{result.outcome}",
                    extra={"candidates": [c.model_dump(mode="json") for c in result.candidates]},
                )
            product_id = result.match.card_product_id
            source, confidence = result.source, result.match.confidence
            product = await self.session.get(CardProduct, product_id)
            if product is None:
                raise NotFoundError("Card product not found.")

        existing = await self.session.scalar(
            select(UserCard).where(
                UserCard.user_id == user.id,
                UserCard.card_product_id == product.id,
                UserCard.bin == request.bin,
            )
        )
        if existing is not None:
            existing.is_active = True
            existing.nickname = request.nickname or existing.nickname
            await self.session.commit()
            return existing
        card = UserCard(
            user_id=user.id,
            card_product_id=product.id,
            bin=request.bin,
            nickname=request.nickname,
            match_source=source,
            match_confidence=confidence,
        )
        card.card_product = product
        self.session.add(card)
        await self.session.commit()
        return card

    async def list_cards(self, user: User) -> list[UserCard]:
        result = await self.session.scalars(
            select(UserCard)
            .where(UserCard.user_id == user.id, UserCard.is_active.is_(True))
            .order_by(UserCard.created_at)
        )
        return list(result.unique())

    async def get_card(self, user: User, card_id: uuid.UUID) -> UserCard:
        card = await self.session.get(UserCard, card_id)
        if card is None or card.user_id != user.id or not card.is_active:
            raise NotFoundError("Card not found.", code="card_not_found")
        return card

    async def remove_card(self, user: User, card_id: uuid.UUID) -> None:
        card = await self.get_card(user, card_id)
        card.is_active = False
        await self.session.commit()

    async def coverage(self, user: User, card_id: uuid.UUID, language: Language) -> CoverageView:
        card = await self.get_card(user, card_id)
        policy_id = card.card_product.policy_id
        policy = await self.session.get(Policy, policy_id) if policy_id else None
        if policy is None or not policy.is_active:
            raise NotFoundError("This card has no coverage on file.", code="coverage_not_found")
        return await KnowledgeService(self.session).coverage_view(policy, language)

    async def policy_scope(
        self, user: User, *, card_id: uuid.UUID | None = None
    ) -> list[ScopedPolicy]:
        return await resolve_policy_scope(self.session, user.id, card_id=card_id)

    async def list_reviews(self, params: PageParams, status: BinReviewStatus) -> Page[Any]:
        query = select(BinReview).where(BinReview.status == status).order_by(BinReview.created_at)
        return await paginate(self.session, query, params)

    async def resolve_review(
        self, review_id: uuid.UUID, product_id: uuid.UUID, *, actor_id: uuid.UUID
    ) -> BinReview:
        review = await self._review(review_id)
        product = await self.session.get(CardProduct, product_id)
        if product is None or product.country_code != review.country_code:
            raise UnprocessableError("Pick a card product from the same country.")
        await self._cache(review.bin, review.country_code, product.id, 100.0, MatchSource.ADMIN)
        review.status = BinReviewStatus.RESOLVED
        review.resolved_card_product_id = product.id
        review.resolved_by_id = actor_id
        review.resolved_at = datetime.now(UTC)
        await self.session.commit()
        return review

    async def dismiss_review(self, review_id: uuid.UUID, *, actor_id: uuid.UUID) -> BinReview:
        review = await self._review(review_id)
        review.status = BinReviewStatus.DISMISSED
        review.resolved_by_id = actor_id
        review.resolved_at = datetime.now(UTC)
        await self.session.commit()
        return review

    async def _review(self, review_id: uuid.UUID) -> BinReview:
        review = await self.session.get(BinReview, review_id)
        if review is None:
            raise NotFoundError("Review not found.", code="bin_review_not_found")
        if review.status is not BinReviewStatus.OPEN:
            raise ConflictError("Review is already closed.", code="bin_review_closed")
        return review

    async def _cache(
        self,
        bin_number: str,
        country_code: str,
        product_id: uuid.UUID,
        confidence: float,
        source: MatchSource = MatchSource.LOOKUP,
    ) -> None:
        statement = insert(BinMapping).values(
            bin=bin_number,
            country_code=country_code,
            card_product_id=product_id,
            confidence=confidence,
            source=source,
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["bin", "country_code"],
                set_={
                    "card_product_id": statement.excluded.card_product_id,
                    "confidence": statement.excluded.confidence,
                    "source": statement.excluded.source,
                },
            )
        )

    async def _queue_review(
        self,
        bin_number: str,
        country_code: str,
        reason: BinReviewReason,
        candidates: list[CandidateCard],
        records: list[BinRecord],
    ) -> None:
        existing = await self.session.scalar(
            select(BinReview).where(
                BinReview.bin == bin_number,
                BinReview.country_code == country_code,
                BinReview.status == BinReviewStatus.OPEN,
            )
        )
        payload_candidates = [c.model_dump(mode="json") for c in candidates]
        payload_records = [record.raw or asdict(record) for record in records]
        if existing is not None:
            existing.candidates = payload_candidates or existing.candidates
            existing.lookup_response = payload_records or existing.lookup_response
            return
        self.session.add(
            BinReview(
                bin=bin_number,
                country_code=country_code,
                reason=reason,
                candidates=payload_candidates,
                lookup_response=payload_records,
            )
        )

    @staticmethod
    def _country(user: User, country: str | None) -> str:
        code = (country or user.country_code or "").upper()
        if len(code) != 2:
            raise UnprocessableError(
                "A country is needed to identify cards.", code="country_required"
            )
        return code
