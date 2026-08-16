from __future__ import annotations

import pytest

from app import agent as agent_module
from app.models import (
    Consensus,
    EvacLevel,
    Record,
    RouteCandidate,
    Shelter,
    SourceId,
    Verdict,
)
from app.session import EvacuationSession


def _verdict(level: EvacLevel) -> Verdict:
    return Verdict(recommended_action="test", headline=level.label, level=level)


def _consensus(level: EvacLevel) -> Consensus:
    return Consensus(agreed=True, confidence="high", level=level)


def _shelter() -> Shelter:
    return Shelter(
        shelter_id="safe",
        name="Safe shelter",
        lat=47.65,
        lon=-117.30,
        record=Record(record_id="shelter", source_id=SourceId.SREC),
    )


def _route() -> RouteCandidate:
    return RouteCandidate(
        route_id="route-a",
        geometry={"type": "LineString", "coordinates": []},
        distance_km=10,
        eta_min=15,
        approved=True,
        record=Record(record_id="route", source_id=SourceId.OSRM),
    )


@pytest.mark.asyncio
async def test_guard_completes_actionable_evacuation_plan(monkeypatch):
    calls: list[str] = []
    session = EvacuationSession()
    shelter = _shelter()
    route = _route()

    async def fake_call(target, name, _args):
        calls.append(name)
        if name == "find_shelters":
            target.shelters = [shelter]
            target.destination = shelter
            return {"preferred_shelter": {"name": shelter.name}, "rejected": []}, 1
        if name == "plan_safe_route":
            target.routes = [route]
            return {"destination": shelter.name, "candidates": [{"route_id": route.route_id}]}, 1
        if name == "validate_route":
            target.approved_routes = [route]
            return {"selected_route_id": route.route_id, "rejected_routes": []}, 1
        raise AssertionError(name)

    monkeypatch.setattr(agent_module, "evaluate_consensus", lambda _ctx: _consensus(EvacLevel.LEVEL_2))
    monkeypatch.setattr(
        agent_module,
        "decide",
        lambda _ctx: (_verdict(EvacLevel.LEVEL_2), [], [], [], []),
    )
    monkeypatch.setattr(agent_module.tools, "call", fake_call)

    subject = object.__new__(agent_module.Agent)
    await subject._backfill(session, set(agent_module.REQUIRED))

    assert calls == ["find_shelters", "plan_safe_route", "validate_route"]
    assert session.current_route is None
    assert any("evacuation plan enforced" in step.label for step in session.steps)


@pytest.mark.asyncio
async def test_guard_does_not_generate_route_when_no_evacuation_applies(monkeypatch):
    async def must_not_call(*_args, **_kwargs):
        raise AssertionError("planning tools must not be backfilled for Level 0")

    monkeypatch.setattr(agent_module, "evaluate_consensus", lambda _ctx: _consensus(EvacLevel.NONE))
    monkeypatch.setattr(
        agent_module,
        "decide",
        lambda _ctx: (_verdict(EvacLevel.NONE), [], [], [], []),
    )
    monkeypatch.setattr(agent_module.tools, "call", must_not_call)

    subject = object.__new__(agent_module.Agent)
    await subject._backfill(EvacuationSession(), set(agent_module.REQUIRED))


@pytest.mark.asyncio
async def test_model_planning_call_is_skipped_after_level_zero_status(monkeypatch):
    session = EvacuationSession(consensus=_consensus(EvacLevel.NONE))

    async def must_not_call(*_args, **_kwargs):
        raise AssertionError("the source adapter must not spend compute")

    monkeypatch.setattr(
        agent_module,
        "decide",
        lambda _ctx: (_verdict(EvacLevel.NONE), [], [], [], []),
    )
    monkeypatch.setattr(agent_module.tools, "call", must_not_call)

    subject = object.__new__(agent_module.Agent)
    result, latency_ms = await subject._call_model_tool(
        session, "find_shelters", {}
    )

    assert result["planning_skipped"] is True
    assert "no evacuation route is required" in result["reason"]
    assert latency_ms == 0


def test_non_actionable_verdict_drops_model_narrative(monkeypatch):
    monkeypatch.setattr(
        agent_module,
        "decide",
        lambda _ctx: (_verdict(EvacLevel.NONE), [], [], [], []),
    )
    monkeypatch.setattr(EvacuationSession, "persist", lambda _self: None)
    session = EvacuationSession()

    subject = object.__new__(agent_module.Agent)
    subject._finalize(session, "Unsupported model claim")

    assert session.verdict is not None
    assert session.verdict.narrative is None
