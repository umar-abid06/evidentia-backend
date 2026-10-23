from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from evidentia.api.deps import SessionDep
from evidentia.core.i18n import Language, as_language
from evidentia.modules.auth.dependencies import CurrentUser
from evidentia.modules.travel.models import JetLagPlan, Trip
from evidentia.modules.travel.schemas import (
    GuideView,
    JetLagPlanCreate,
    JetLagPlanRead,
    TripCreate,
    TripRead,
)
from evidentia.modules.travel.service import TravelService


def get_travel_service(session: SessionDep) -> TravelService:
    return TravelService(session)


TravelDep = Annotated[TravelService, Depends(get_travel_service)]

router = APIRouter(tags=["travel"])


@router.post("/trips", response_model=TripRead, status_code=status.HTTP_201_CREATED)
async def create_trip(payload: TripCreate, user: CurrentUser, service: TravelDep) -> Trip:
    """Creates a trip and makes sure a health guide exists for its destination."""
    return await service.create_trip(user, payload)


@router.get("/trips", response_model=list[TripRead])
async def list_trips(user: CurrentUser, service: TravelDep) -> list[Trip]:
    return await service.list_trips(user)


@router.get("/trips/{trip_id}", response_model=TripRead)
async def get_trip(trip_id: uuid.UUID, user: CurrentUser, service: TravelDep) -> Trip:
    return await service.get_trip(user, trip_id)


@router.get("/trips/{trip_id}/health-guide", response_model=GuideView)
async def trip_health_guide(
    trip_id: uuid.UUID,
    user: CurrentUser,
    service: TravelDep,
    language: Annotated[Language | None, Query()] = None,
) -> GuideView:
    return await service.trip_guide(user, trip_id, language or as_language(user.preferred_language))


@router.post("/trips/{trip_id}/health-guide/accept-update", response_model=TripRead)
async def accept_guide_update(trip_id: uuid.UUID, user: CurrentUser, service: TravelDep) -> Trip:
    return await service.accept_guide_update(user, trip_id)


@router.post("/jetlag-plans", response_model=JetLagPlanRead, status_code=status.HTTP_202_ACCEPTED)
async def create_jetlag_plan(
    payload: JetLagPlanCreate, user: CurrentUser, service: TravelDep
) -> JetLagPlan:
    """Computes the time-zone maths now; the coaching text is generated in the background."""
    return await service.create_plan(user, payload)


@router.get("/jetlag-plans/{plan_id}", response_model=JetLagPlanRead)
async def get_jetlag_plan(plan_id: uuid.UUID, user: CurrentUser, service: TravelDep) -> JetLagPlan:
    return await service.get_plan(user, plan_id)
