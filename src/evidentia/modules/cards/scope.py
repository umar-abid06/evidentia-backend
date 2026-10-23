"""Resolves which published policies a customer holds, before any search runs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evidentia.modules.cards.models import UserCard
from evidentia.modules.catalog.models import CardProduct, Policy
from evidentia.modules.knowledge.models import PolicyVersion, VersionStatus


@dataclass(frozen=True, slots=True)
class ScopedPolicy:
    """A policy the signed-in customer holds, with its customer-facing names."""

    policy_id: uuid.UUID
    version_id: uuid.UUID
    policy_name: str
    card_id: uuid.UUID
    card_name: str


async def resolve_policy_scope(
    session: AsyncSession, user_id: uuid.UUID, *, card_id: uuid.UUID | None = None
) -> list[ScopedPolicy]:
    """One selected card, or every active card; only live (ready) versions qualify."""
    query = (
        select(UserCard.id, CardProduct.display_name, Policy, PolicyVersion.id)
        .join(CardProduct, CardProduct.id == UserCard.card_product_id)
        .join(Policy, Policy.id == CardProduct.policy_id)
        .join(
            PolicyVersion,
            (PolicyVersion.policy_id == Policy.id) & (PolicyVersion.status == VersionStatus.READY),
        )
        .where(
            UserCard.user_id == user_id,
            UserCard.is_active.is_(True),
            CardProduct.is_active.is_(True),
            Policy.is_active.is_(True),
        )
        .order_by(UserCard.created_at)
    )
    if card_id is not None:
        query = query.where(UserCard.id == card_id)
    scoped: dict[uuid.UUID, ScopedPolicy] = {}
    for card, card_name, policy, version_id in (await session.execute(query)).all():
        scoped.setdefault(
            policy.id,
            ScopedPolicy(
                policy_id=policy.id,
                version_id=version_id,
                policy_name=policy.display_name,
                card_id=card,
                card_name=card_name,
            ),
        )
    return list(scoped.values())
