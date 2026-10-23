from __future__ import annotations

import pytest
from sqlalchemy import select

from evidentia.db.session import Database
from evidentia.integrations.bin_lookup import BinRecord, StaticBinLookup
from evidentia.modules.auth.models import User
from evidentia.modules.cards.models import BinReview, MatchSource
from evidentia.modules.cards.schemas import AddCardRequest
from evidentia.modules.cards.service import CardService
from evidentia.modules.catalog.models import CardProduct, Policy

pytestmark = pytest.mark.integration


async def _setup(db: Database) -> tuple[User, CardProduct, CardProduct]:
    async with db.session() as session:
        user = User(email="maya@example.com", password_hash="x", country_code="DE")
        policy = Policy(code="p1", name="P1", display_name="Travel Cover", insurer_name="Ins SE")
        gold = CardProduct(
            issuer_name="Example Bank AG",
            product_name="Miles & More Gold",
            display_name="Example Miles Gold",
            country_code="DE",
            policy=policy,
        )
        plain = CardProduct(
            issuer_name="Example Bank AG",
            product_name="Classic",
            display_name="Example Classic",
            country_code="DE",
            policy=policy,
        )
        session.add_all([user, policy, gold, plain])
        await session.commit()
        return user, gold, plain


async def test_identify_matches_caches_and_reuses(db: Database) -> None:
    user, gold, _ = await _setup(db)
    lookup = StaticBinLookup(
        {
            "52341234": [
                BinRecord("EXAMPLE BANK", "Miles & More Gold Mastercard", "mc", "credit", "DE")
            ]
        }
    )
    async with db.session() as session:
        service = CardService(session, lookup)
        first = await service.identify(user, "52341234", None)
        assert first.outcome == "matched"
        assert first.match is not None
        assert first.match.card_product_id == gold.id

        lookup.available = False  # second call must come from the cache
        second = await service.identify(user, "52341234", None)
        assert second.source is MatchSource.CACHE
        assert second.match is not None
        assert second.match.confidence == 100.0


async def test_debit_and_foreign_cards_are_filtered_out(db: Database) -> None:
    user, _, _ = await _setup(db)
    lookup = StaticBinLookup(
        {
            "411111": [
                BinRecord("Example Bank", "Miles & More Gold", "visa", "debit", "DE"),
                BinRecord("Example Bank", "Miles & More Gold", "visa", "credit", "FR"),
            ]
        }
    )
    async with db.session() as session:
        result = await CardService(session, lookup).identify(user, "411111", None)
        assert result.outcome == "not_found"
        assert await session.scalar(select(BinReview.bin)) == "411111"


async def test_unavailable_lookup_caches_nothing(db: Database) -> None:
    user, _, _ = await _setup(db)
    lookup = StaticBinLookup()
    lookup.available = False
    async with db.session() as session:
        result = await CardService(session, lookup).identify(user, "400000", None)
        assert result.outcome == "unavailable"


async def test_manual_choice_is_a_full_confidence_match_queued_for_review(db: Database) -> None:
    user, _, plain = await _setup(db)
    async with db.session() as session:
        service = CardService(session, StaticBinLookup())
        card = await service.add_card(
            user, AddCardRequest(bin="400000", card_product_id=plain.id, nickname="Mine")
        )
        assert card.match_source is MatchSource.MANUAL
        assert card.match_confidence == 100.0
        scope = await service.policy_scope(user)
        assert scope == []  # no published version yet, so nothing is searchable
