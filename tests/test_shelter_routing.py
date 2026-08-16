from __future__ import annotations

import pytest

from app import tools
from app.models import (
    EvacLevel,
    EvacZone,
    HouseholdNeeds,
    Place,
    Record,
    Shelter,
    SourceId,
    utcnow,
)
from app.session import EvacuationSession


def _record(*, geometry=None) -> Record:
    return Record(
        record_id="test:record",
        source_id=SourceId.SREC,
        observed_at=utcnow(),
        geometry=geometry,
    )


def _zone() -> EvacZone:
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [-117.55, 47.68],
                [-117.43, 47.68],
                [-117.43, 47.77],
                [-117.55, 47.77],
                [-117.55, 47.68],
            ]
        ],
    }
    return EvacZone(
        zone_id="level-3",
        level=EvacLevel.LEVEL_3,
        boundary_desc="Test red zone",
        record=_record(geometry=geometry),
    )


def _shelter(name: str, shelter_id: str, lat: float, lon: float) -> Shelter:
    return Shelter(
        shelter_id=shelter_id,
        name=name,
        lat=lat,
        lon=lon,
        distance_km=2.0,
        capabilities=["pets"],
        record=_record(),
    )


@pytest.mark.asyncio
async def test_no_safe_shelter_skips_router_computation(monkeypatch):
    unsafe = _shelter("Inside red zone", "unsafe", 47.72, -117.49)
    session = EvacuationSession(
        place=Place(lat=47.72, lon=-117.49),
        needs=HouseholdNeeds(pets=True),
        shelters=[unsafe],
        zones=[_zone()],
        destination=unsafe,
    )

    async def router_must_not_run(*args, **kwargs):
        raise AssertionError("router must not run without an eligible destination")

    monkeypatch.setattr(tools.osrm, "plan_routes", router_must_not_run)
    monkeypatch.setattr(tools.mapbox, "plan_routes", router_must_not_run)

    result = await tools.plan_safe_route(session)

    assert result["routing_skipped"] is True
    assert result["candidates"] == []
    assert "no shelter outside" in result["error"]
    assert session.destination is None


@pytest.mark.asyncio
async def test_requested_unsafe_shelter_cannot_bypass_safe_alternative(monkeypatch):
    unsafe = _shelter("Inside red zone", "unsafe", 47.72, -117.49)
    safe = _shelter("Outside red zone", "safe", 47.66, -117.30)
    session = EvacuationSession(
        place=Place(lat=47.72, lon=-117.49),
        needs=HouseholdNeeds(pets=True),
        shelters=[unsafe, safe],
        zones=[_zone()],
        destination=safe,
    )

    async def router_must_not_run(*args, **kwargs):
        raise AssertionError("router must not run for a rejected shelter")

    monkeypatch.setattr(tools.osrm, "plan_routes", router_must_not_run)
    monkeypatch.setattr(tools.mapbox, "plan_routes", router_must_not_run)

    result = await tools.plan_safe_route(session, shelter_id="unsafe")

    assert result["routing_skipped"] is True
    assert "inside Level 3 zone" in result["error"]
    assert result["candidates"] == []
