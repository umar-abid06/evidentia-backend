from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from evidentia.api.deps import SessionDep
from evidentia.api.pagination import Page, PageDep
from evidentia.modules.auth.dependencies import AdminUser, StaffUser
from evidentia.modules.catalog.models import CardProduct, Policy
from evidentia.modules.catalog.schemas import (
    CardProductCreate,
    CardProductRead,
    CardProductUpdate,
    PolicyCreate,
    PolicyRead,
    PolicyUpdate,
)
from evidentia.modules.catalog.service import CatalogService


def get_catalog_service(session: SessionDep) -> CatalogService:
    return CatalogService(session)


CatalogDep = Annotated[CatalogService, Depends(get_catalog_service)]

router = APIRouter(prefix="/admin", tags=["admin: catalog"])


@router.post("/policies", response_model=PolicyRead, status_code=status.HTTP_201_CREATED)
async def create_policy(payload: PolicyCreate, _: AdminUser, service: CatalogDep) -> Policy:
    return await service.create_policy(payload)


@router.get("/policies", response_model=Page[PolicyRead])
async def list_policies(
    _: StaffUser,
    service: CatalogDep,
    page: PageDep,
    active: Annotated[bool | None, Query()] = None,
) -> Page[Any]:
    return await service.list_policies(page, active=active)


@router.get("/policies/{policy_id}", response_model=PolicyRead)
async def get_policy(policy_id: uuid.UUID, _: StaffUser, service: CatalogDep) -> Policy:
    return await service.get_policy(policy_id)


@router.patch("/policies/{policy_id}", response_model=PolicyRead)
async def update_policy(
    policy_id: uuid.UUID, payload: PolicyUpdate, _: AdminUser, service: CatalogDep
) -> Policy:
    return await service.update_policy(policy_id, payload)


@router.post("/card-products", response_model=CardProductRead, status_code=status.HTTP_201_CREATED)
async def create_card_product(
    payload: CardProductCreate, _: AdminUser, service: CatalogDep
) -> CardProduct:
    return await service.create_card_product(payload)


@router.get("/card-products", response_model=Page[CardProductRead])
async def list_card_products(
    _: StaffUser,
    service: CatalogDep,
    page: PageDep,
    country_code: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
) -> Page[Any]:
    return await service.list_card_products(page, country_code=country_code)


@router.patch("/card-products/{product_id}", response_model=CardProductRead)
async def update_card_product(
    product_id: uuid.UUID, payload: CardProductUpdate, _: AdminUser, service: CatalogDep
) -> CardProduct:
    return await service.update_card_product(product_id, payload)
