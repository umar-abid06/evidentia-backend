from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from evidentia.api.pagination import Page, PageParams, paginate
from evidentia.core.errors import ConflictError, NotFoundError
from evidentia.modules.catalog.models import CardProduct, Policy
from evidentia.modules.catalog.schemas import (
    CardProductCreate,
    CardProductUpdate,
    PolicyCreate,
    PolicyUpdate,
)


class CatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_policy(self, data: PolicyCreate) -> Policy:
        policy = Policy(**data.model_dump())
        await self._insert(policy, conflict="A policy with this code already exists.")
        return policy

    async def get_policy(self, policy_id: uuid.UUID) -> Policy:
        policy = await self.session.get(Policy, policy_id)
        if policy is None:
            raise NotFoundError("Policy not found.", code="policy_not_found")
        return policy

    async def list_policies(self, params: PageParams, *, active: bool | None = None) -> Page[Any]:
        query = select(Policy).order_by(Policy.code)
        if active is not None:
            query = query.where(Policy.is_active.is_(active))
        return await paginate(self.session, query, params)

    async def update_policy(self, policy_id: uuid.UUID, data: PolicyUpdate) -> Policy:
        policy = await self.get_policy(policy_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(policy, field, value)
        await self.session.commit()
        return policy

    async def is_policy_live_for_cards(self, policy_id: uuid.UUID) -> bool:
        """True when the policy is active and at least one active card carries it."""
        query = select(
            exists().where(
                Policy.id == policy_id,
                Policy.is_active.is_(True),
                CardProduct.policy_id == Policy.id,
                CardProduct.is_active.is_(True),
            )
        )
        return bool(await self.session.scalar(query))

    async def create_card_product(self, data: CardProductCreate) -> CardProduct:
        if data.policy_id is not None:
            await self.get_policy(data.policy_id)
        product = CardProduct(**data.model_dump())
        await self._insert(product, conflict="This card product already exists.")
        return product

    async def get_card_product(self, product_id: uuid.UUID) -> CardProduct:
        product = await self.session.get(CardProduct, product_id)
        if product is None:
            raise NotFoundError("Card product not found.", code="card_product_not_found")
        return product

    async def list_card_products(
        self, params: PageParams, *, country_code: str | None = None
    ) -> Page[Any]:
        query = select(CardProduct).order_by(CardProduct.issuer_name, CardProduct.product_name)
        if country_code:
            query = query.where(CardProduct.country_code == country_code.upper())
        return await paginate(self.session, query, params)

    async def update_card_product(
        self, product_id: uuid.UUID, data: CardProductUpdate
    ) -> CardProduct:
        product = await self.get_card_product(product_id)
        changes = data.model_dump(exclude_unset=True)
        if changes.get("policy_id") is not None:
            await self.get_policy(changes["policy_id"])
        for field, value in changes.items():
            setattr(product, field, value)
        await self.session.commit()
        return product

    async def _insert(self, entity: Policy | CardProduct, *, conflict: str) -> None:
        self.session.add(entity)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(conflict) from exc
