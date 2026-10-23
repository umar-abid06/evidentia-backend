from __future__ import annotations

from datetime import date

import pytest

from evidentia.core.tasks import RecordingDispatcher
from evidentia.db.session import Database
from evidentia.modules.auth.models import User
from evidentia.modules.travel.schemas import TripCreate
from evidentia.modules.travel.service import TravelService
from evidentia.workers.names import GENERATE_HEALTH_GUIDE

pytestmark = pytest.mark.integration


async def test_trips_to_one_destination_share_a_single_guide(
    db: Database, dispatcher: RecordingDispatcher
) -> None:
    async with db.session() as session:
        user = User(email="t@example.com", password_hash="x", country_code="DE")
        session.add(user)
        await session.commit()

        service = TravelService(session)
        trip = TripCreate(
            destination_country="TH", start_date=date(2026, 12, 1), end_date=date(2026, 12, 14)
        )
        first = await service.create_trip(user, trip)
        second = await service.create_trip(user, trip)

        assert first.health_guide_id == second.health_guide_id
        assert dispatcher.names() == [GENERATE_HEALTH_GUIDE]

        view = await service.trip_guide(user, first.id, "de")
        assert view.status == "pending"
        assert view.content is None
        assert dispatcher.names().count(GENERATE_HEALTH_GUIDE) == 2  # German translation queued
