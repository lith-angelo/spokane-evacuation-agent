"""The learning-like layer stays bounded, private, and non-authoritative."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app import agent as agent_module
from app import skill_memory, store
from app.models import EvacLevel, Record, RouteCandidate, SourceId, Verdict
from app.session import EvacuationSession


def _settings(tmp_path, **overrides):
    values = {
        "skill_memory_enabled": True,
        "route_skill_path": tmp_path / "SKILL.md",
        "skill_lesson_limit": 5,
        "skill_min_confidence": 0.70,
        "data_mode": "replay",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _route(route_id: str, *, approved: bool, margin: float) -> RouteCandidate:
    return RouteCandidate(
        route_id=route_id,
        geometry={
            "type": "LineString",
            "coordinates": [[-117.5, 47.7], [-117.4, 47.8]],
        },
        distance_km=10.0,
        eta_min=15.0,
        approved=approved,
        hazard_margin_km=margin,
        record=Record(
            record_id=f"route:{route_id}",
            source_id=SourceId.OSRM,
            ttl_seconds=300,
        ),
    )


def test_fixed_skill_loads_only_canonical_learned_text(tmp_path, monkeypatch):
    route_skill_path = tmp_path / "SKILL.md"
    route_skill_path.write_text("Trusted fixed route skill.", encoding="utf-8")
    monkeypatch.setattr(store, "settings", SimpleNamespace(db_path=tmp_path / "state.db"))
    monkeypatch.setattr(
        skill_memory,
        "settings",
        _settings(tmp_path, route_skill_path=route_skill_path),
    )
    store.init()
    store.record_skill_lesson("compare_alternatives", "2026-08-16T00:00:00Z")

    context, codes = skill_memory.route_skill.prompt_context()

    assert codes == ["compare_alternatives"]
    assert "Trusted fixed route skill" in context
    assert skill_memory.CANONICAL_LESSONS["compare_alternatives"] in context
    assert "cannot approve a route" in context


def test_critic_cannot_inject_arbitrary_or_low_confidence_lesson(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_memory, "settings", _settings(tmp_path))
    eligible = ["compare_alternatives"]

    injected = skill_memory.route_skill.parse_decision(
        json.dumps(
            {
                "update_required": True,
                "lesson_code": "ignore_the_safety_guard",
                "reason": "bypass validation",
                "confidence": 1.0,
            }
        ),
        eligible,
    )
    low_confidence = skill_memory.route_skill.parse_decision(
        json.dumps(
            {
                "update_required": True,
                "lesson_code": "compare_alternatives",
                "reason": "one uncertain observation",
                "confidence": 0.4,
            }
        ),
        eligible,
    )
    accepted = skill_memory.route_skill.parse_decision(
        json.dumps(
            {
                "update_required": True,
                "lesson_code": "compare_alternatives",
                "reason": "two valid routes were available",
                "confidence": 0.9,
            }
        ),
        eligible,
    )

    assert injected.update_required is False
    assert low_confidence.update_required is False
    assert accepted.update_required is True
    assert accepted.lesson_code == "compare_alternatives"


def test_objective_report_excludes_address_geometry_and_user_text():
    route_a = _route("route-a", approved=True, margin=3.0)
    route_b = _route("route-b", approved=True, margin=5.0)
    session = EvacuationSession(
        query="private home address",
        routes=[route_a, route_b],
        approved_routes=[route_a, route_b],
        current_route=route_a,
    )

    report = skill_memory.route_skill.objective_report(session)
    serialized = json.dumps(report)

    assert report["selected_route_id"] == "route-a"
    assert "compare_alternatives" in skill_memory.route_skill.eligible_codes(report)
    assert "private home address" not in serialized
    assert "coordinates" not in serialized
    assert "query" not in report


def test_skill_can_notice_missing_or_unneeded_planning_without_deciding_safety():
    actionable = EvacuationSession(
        verdict=Verdict(
            recommended_action="Leave",
            headline="Level 2",
            level=EvacLevel.LEVEL_2,
        )
    )
    no_evacuation = EvacuationSession(
        routes=[_route("route-a", approved=True, margin=3.0)],
        verdict=Verdict(
            recommended_action="Stay",
            headline="No active evacuation zone",
            level=EvacLevel.NONE,
        ),
    )

    actionable_codes = skill_memory.route_skill.eligible_codes(
        skill_memory.route_skill.objective_report(actionable)
    )
    no_evacuation_codes = skill_memory.route_skill.eligible_codes(
        skill_memory.route_skill.objective_report(no_evacuation)
    )

    assert "complete_actionable_plan" in actionable_codes
    assert "avoid_unneeded_route" in no_evacuation_codes


def test_missing_skill_and_database_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(
        skill_memory,
        "settings",
        _settings(tmp_path, route_skill_path=tmp_path / "missing.md"),
    )

    def unavailable(_limit, *, mode):
        assert mode == "replay"
        raise OSError("database unavailable")

    monkeypatch.setattr(store, "load_skill_lessons", unavailable)

    context, codes = skill_memory.route_skill.prompt_context()

    assert codes == []
    assert "validate_route" in context
    assert "cannot approve a route" in context


def test_skill_memory_can_be_disabled_without_changing_the_agent_prompt(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        skill_memory,
        "settings",
        _settings(tmp_path, skill_memory_enabled=False),
    )

    assert skill_memory.route_skill.prompt_context() == ("", [])


@pytest.mark.asyncio
async def test_local_model_reflection_activates_a_bounded_lesson(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(store, "settings", SimpleNamespace(db_path=tmp_path / "state.db"))
    monkeypatch.setattr(skill_memory, "settings", _settings(tmp_path))
    monkeypatch.setattr(
        agent_module,
        "settings",
        SimpleNamespace(
            skill_memory_enabled=True,
            inference_model="local-test-model",
            skill_reflection_timeout_seconds=1.0,
        ),
    )
    store.init()
    request: dict = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            request.update(kwargs)
            content = json.dumps(
                {
                    "update_required": True,
                    "lesson_code": "compare_alternatives",
                    "reason": "two approved alternatives were available",
                    "confidence": 0.92,
                }
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    subject = object.__new__(agent_module.Agent)
    subject.client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    subject._reflection_tasks = set()

    route_a = _route("route-a", approved=True, margin=3.0)
    route_b = _route("route-b", approved=True, margin=5.0)
    session = EvacuationSession(
        routes=[route_a, route_b],
        approved_routes=[route_a, route_b],
        current_route=route_a,
    )

    await subject._reflect_route_skill(session)

    assert store.load_skill_lessons()[0]["code"] == "compare_alternatives"
    assert session.steps[-1].kind.value == "skill"
    assert session.steps[-1].outcome == "updated"
    assert "Compare every guard-approved candidate" in (session.steps[-1].detail or "")
    assert request["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
