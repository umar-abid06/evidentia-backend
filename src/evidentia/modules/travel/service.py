from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from evidentia.core.errors import NotFoundError, UnprocessableError
from evidentia.core.i18n import Language
from evidentia.core.tasks import enqueue_after_commit
from evidentia.modules.auth.models import User
from evidentia.modules.travel.generation import PLAN_SCHEMA_VERSION
from evidentia.modules.travel.jetlag import UnknownAirportError, compute
from evidentia.modules.travel.models import GenerationStatus, HealthGuide, JetLagPlan, Trip
from evidentia.modules.travel.schemas import GuideView, JetLagPlanCreate, TripCreate
from evidentia.workers.names import GENERATE_HEALTH_GUIDE, GENERATE_JETLAG_PLAN

IN_FLIGHT = (GenerationStatus.PENDING, GenerationStatus.GENERATING)


class TravelService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_trip(self, user: User, data: TripCreate) -> Trip:
        guide = await self.ensure_guide(data.destination_country, data.destination_region)
        trip = Trip(
            user_id=user.id,
            destination_country=data.destination_country,
            destination_region=data.destination_region,
            start_date=data.start_date,
            end_date=data.end_date,
            health_guide_id=guide.id,
        )
        self.session.add(trip)
        await self.session.commit()
        return trip

    async def list_trips(self, user: User) -> list[Trip]:
        result = await self.session.scalars(
            select(Trip).where(Trip.user_id == user.id).order_by(Trip.start_date)
        )
        return list(result)

    async def get_trip(self, user: User, trip_id: uuid.UUID) -> Trip:
        trip = await self.session.get(Trip, trip_id)
        if trip is None or trip.user_id != user.id:
            raise NotFoundError("Trip not found.", code="trip_not_found")
        return trip

    async def ensure_guide(self, country: str, region: str) -> HealthGuide:
        """Generate once per destination: reuse a fresh guide or one already in flight."""
        latest = await self._latest(country, region, "en")
        now = datetime.now(UTC)
        if latest is not None and (
            latest.status in IN_FLIGHT
            or (
                latest.status is GenerationStatus.READY
                and (latest.expires_at is None or latest.expires_at > now)
            )
        ):
            return latest
        version = (latest.version + 1) if latest else 1
        return await self._create_guide(country, region, "en", version)

    async def trip_guide(self, user: User, trip_id: uuid.UUID, language: Language) -> GuideView:
        trip = await self.get_trip(user, trip_id)
        # Loaded explicitly: async sessions must never fall back to lazy loading.
        current = (
            await self.session.get(HealthGuide, trip.health_guide_id)
            if trip.health_guide_id
            else None
        )
        if current is None:
            current = await self.ensure_guide(trip.destination_country, trip.destination_region)
            trip.health_guide_id = current.id
        newest = await self._latest(
            trip.destination_country, trip.destination_region, "en", ready_only=True
        )
        guide = current if language == "en" else await self._translation(current, language)
        await self.session.commit()
        return GuideView(
            guide_id=guide.id,
            language=language,
            version=guide.version,
            status=guide.status,
            content=guide.content if guide.status is GenerationStatus.READY else None,
            expires_at=current.expires_at,
            update_available=newest is not None and newest.version > current.version,
        )

    async def accept_guide_update(self, user: User, trip_id: uuid.UUID) -> Trip:
        trip = await self.get_trip(user, trip_id)
        newest = await self._latest(
            trip.destination_country, trip.destination_region, "en", ready_only=True
        )
        if newest is not None:
            trip.health_guide_id = newest.id
        await self.session.commit()
        return trip

    async def create_plan(self, user: User, data: JetLagPlanCreate) -> JetLagPlan:
        if data.trip_id is not None:
            await self.get_trip(user, data.trip_id)
        try:
            metrics = compute(data.segments)
        except (UnknownAirportError, ValueError) as exc:
            raise UnprocessableError(str(exc), code="invalid_itinerary") from exc
        plan = JetLagPlan(
            user_id=user.id,
            trip_id=data.trip_id,
            segments=[segment.model_dump(mode="json") for segment in data.segments],
            metrics=metrics.model_dump(mode="json"),
            schema_version=PLAN_SCHEMA_VERSION,
        )
        self.session.add(plan)
        await self.session.flush()
        enqueue_after_commit(self.session, GENERATE_JETLAG_PLAN, str(plan.id))
        await self.session.commit()
        return plan

    async def get_plan(self, user: User, plan_id: uuid.UUID) -> JetLagPlan:
        plan = await self.session.get(JetLagPlan, plan_id)
        if plan is None or plan.user_id != user.id:
            raise NotFoundError("Plan not found.", code="plan_not_found")
        return plan

    async def _translation(self, source: HealthGuide, language: Language) -> HealthGuide:
        existing = await self.session.scalar(
            select(HealthGuide).where(
                HealthGuide.country_code == source.country_code,
                HealthGuide.region_code == source.region_code,
                HealthGuide.language == language,
                HealthGuide.version == source.version,
            )
        )
        if existing is not None:
            return existing
        return await self._create_guide(
            source.country_code, source.region_code, language, source.version
        )

    async def _create_guide(
        self, country: str, region: str, language: str, version: int
    ) -> HealthGuide:
        guide = HealthGuide(
            country_code=country, region_code=region, language=language, version=version
        )
        try:
            async with self.session.begin_nested():
                self.session.add(guide)
        except IntegrityError:
            # Another request created it first; share theirs.
            found = await self._latest(country, region, language)
            if found is None:
                raise
            return found
        enqueue_after_commit(self.session, GENERATE_HEALTH_GUIDE, str(guide.id))
        return guide

    async def _latest(
        self, country: str, region: str, language: str, *, ready_only: bool = False
    ) -> HealthGuide | None:
        query = select(HealthGuide).where(
            HealthGuide.country_code == country,
            HealthGuide.region_code == region,
            HealthGuide.language == language,
        )
        if ready_only:
            query = query.where(HealthGuide.status == GenerationStatus.READY)
        result = await self.session.scalars(query.order_by(HealthGuide.version.desc()).limit(1))
        return result.first()
