"""Offset pagination shared by list endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


class PageParams(BaseModel):
    limit: int = 50
    offset: int = 0


def page_params(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


async def paginate(session: AsyncSession, query: Select[Any], params: PageParams) -> Page[Any]:
    total = await session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = await session.scalars(query.limit(params.limit).offset(params.offset))
    return Page(items=list(rows), total=total or 0, limit=params.limit, offset=params.offset)


PageDep = Annotated[PageParams, Depends(page_params)]
