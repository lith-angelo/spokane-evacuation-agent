"""Regression tests for always-on route revalidation."""

from __future__ import annotations

import pytest

from app import monitor
from app.models import (
    Consensus,
    EvacLevel,
    HouseholdNeeds,
    Incident,
    Place,
    Record,
    RouteCandidate,
    SourceId,
)
from app.session import EvacuationSession


def _record(source: SourceId, record_id: str, geometry=None) -> Record:
    return Record(
        record_id=record_id,
        source_id=source,
        ttl_seconds=3600,
        geometry=geometry,
    )


def _route() -> RouteCandidate:
    return RouteCandidate(
        route_id="route-a",
        geometry={
            "type": "LineString",
            "coordinates": [[-117.50, 47.72], [-117.40, 47.72]],
        },
        distance_km=8.0,
        eta_min=12.0,
        approved=True,
        record=_record(SourceId.OSRM, "route:a"),
    )


def _incident(perimeter) -> Incident:
    return Incident(
        incident_id="fire:1",
        name="Same Named Fire",
        distance_km=4.0,
        perimeter=perimeter,
        record=_record(SourceId.WFIGS, "fire:1", perimeter),
    )


async def _unchanged(_session):
    return {}


async def _no_new_route(session):
    session.routes = []
    return {"candidates": []}


def _level_result():
    return {"level": EvacLevel.NONE.label}


@pytest.mark.asyncio
async def test_existing_fire_growth_invalidates_route_without_new_incident(monkeypatch):
    """A perimeter revision must bite even when the incident name is unchanged."""
    safe_perimeter = {
        "type": "Polygon",
        "coordinates": [
            [
                [-117.70, 47.80],
                [-117.65, 47.80],
                [-117.65, 47.85],
                [-117.70, 47.85],
                [-117.70, 47.80],
            ]
        ],
    }
    grown_perimeter = {
        "type": "Polygon",
        "coordinates": [
            [
                [-117.47, 47.70],
                [-117.43, 47.70],
                [-117.43, 47.74],
                [-117.47, 47.74],
                [-117.47, 47.70],
            ]
        ],
    }

    route = _route()
    session = EvacuationSession(
        query="test location",
        place=Place(lat=47.72, lon=-117.50, label="Test"),
        needs=HouseholdNeeds(),
        incidents=[_incident(safe_perimeter)],
        routes=[route],
        approved_routes=[route],
        current_route=route,
    )

    async def grow_existing_fire(target):
        target.incidents = [_incident(grown_perimeter)]
        return {"count": 1}

    async def keep_level(_target):
        return _level_result()

    monkeypatch.setattr(monitor.tools, "get_closures", _unchanged)
    monkeypatch.setattr(monitor.tools, "get_active_incidents", grow_existing_fire)
    monkeypatch.setattr(monitor.tools, "get_evacuation_status", keep_level)
    monkeypatch.setattr(monitor.tools, "plan_safe_route", _no_new_route)

    event = await monitor.run_once(session)

    assert event is not None
    assert event["previous_route"] == "route-a"
    assert event["replanned"] is True
    assert event["new_route"] is None
    assert "updated hazard geometry" in event["changes"][-1]
    assert any(
        step.label == "Current route route-a invalidated" for step in session.steps
    )


@pytest.mark.asyncio
async def test_unchanged_safe_route_does_not_emit_monitor_event(monkeypatch):
    route = _route()
    session = EvacuationSession(
        query="test location",
        place=Place(lat=47.72, lon=-117.50, label="Test"),
        needs=HouseholdNeeds(),
        routes=[route],
        approved_routes=[route],
        current_route=route,
    )

    async def keep_level(_target):
        return _level_result()

    monkeypatch.setattr(monitor.tools, "get_closures", _unchanged)
    monkeypatch.setattr(monitor.tools, "get_active_incidents", _unchanged)
    monkeypatch.setattr(monitor.tools, "get_evacuation_status", keep_level)

    event = await monitor.run_once(session)

    assert event is None
    assert session.current_route is route
    assert session.monitor_events == []


@pytest.mark.asyncio
async def test_cross_source_escalation_emits_event_without_selected_route(monkeypatch):
    session = EvacuationSession(
        query="test location",
        place=Place(lat=47.72, lon=-117.50, label="Test"),
        needs=HouseholdNeeds(),
        consensus=Consensus(
            agreed=True,
            confidence="high",
            level=EvacLevel.NONE,
            sources_checked=[SourceId.SREC, SourceId.WFIGS],
            explanation="No nearby mapped fire.",
        ),
    )

    async def escalate_consensus(target):
        target.consensus = Consensus(
            agreed=False,
            confidence="low",
            level=EvacLevel.LEVEL_2,
            sources_checked=[SourceId.SREC, SourceId.WFIGS],
            conflicts=["The fire perimeter moved closer."],
            explanation="The conservative reading is Level 2.",
        )
        return {"level": EvacLevel.LEVEL_2.label}

    monkeypatch.setattr(monitor.tools, "get_closures", _unchanged)
    monkeypatch.setattr(monitor.tools, "get_active_incidents", _unchanged)
    monkeypatch.setattr(monitor.tools, "get_evacuation_status", escalate_consensus)

    event = await monitor.run_once(session)

    assert event is not None
    assert event["replanned"] is False
    assert event["changes"] == [
        f"evacuation level changed to {EvacLevel.LEVEL_2.label}"
    ]
