"""Safety-gate tests.

Every gate gets a passing case *and* a negative control — a scenario where the
unsafe answer is the tempting one, asserting that the gate refuses it. The
negative controls are the point: a gate that only ever sees safe inputs proves
nothing.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import (
    BlockedAction,
    Closure,
    EvacLevel,
    EvacZone,
    HouseholdNeeds,
    Incident,
    Record,
    RouteCandidate,
    Shelter,
    SourceId,
    SourceStatus,
    utcnow,
)
from app.safety import (
    HazardContext,
    can_return_home,
    coverage_gaps,
    decide,
    evaluate_consensus,
    filter_shelters,
    validate_route,
)

# --- builders ---------------------------------------------------------------

ORIGIN = (47.7204, -117.4938)


def rec(source=SourceId.SREC, *, age_s=0, ttl=3600, geometry=None, **kw) -> Record:
    return Record(
        record_id=kw.pop("record_id", "test:1"),
        source_id=source,
        observed_at=utcnow() - timedelta(seconds=age_s),
        ttl_seconds=ttl,
        geometry=geometry,
        **kw,
    )


def zone(level: EvacLevel, *, age_s=0, ttl=3600, rings=None) -> EvacZone:
    rings = rings or [
        [-117.60, 47.68],
        [-117.40, 47.68],
        [-117.40, 47.78],
        [-117.60, 47.78],
        [-117.60, 47.68],
    ]
    return EvacZone(
        zone_id=f"z{level.value}",
        level=level,
        boundary_desc="Test zone",
        record=rec(age_s=age_s, ttl=ttl, geometry={"type": "Polygon", "coordinates": [rings]}),
    )


def incident(name="Test Fire", *, distance_km=4.0, perimeter=True, age_s=0, ttl=86400) -> Incident:
    poly = (
        {
            "type": "Polygon",
            "coordinates": [
                [
                    [-117.62, 47.74],
                    [-117.52, 47.74],
                    [-117.52, 47.80],
                    [-117.62, 47.80],
                    [-117.62, 47.74],
                ]
            ],
        }
        if perimeter
        else None
    )
    return Incident(
        incident_id=f"inc:{name}",
        name=name,
        lat=47.77,
        lon=-117.57,
        acres=2840.0,
        containment_pct=5.0,
        distance_km=distance_km,
        perimeter=poly,
        record=rec(SourceId.WFIGS, age_s=age_s, ttl=ttl, geometry=poly),
    )


def shelter(name, caps, *, distance_km=5.0) -> Shelter:
    return Shelter(
        shelter_id=f"s:{name}",
        name=name,
        address=f"{name} address",
        lat=47.66,
        lon=-117.30,
        distance_km=distance_km,
        capabilities=caps,
        record=rec(),
    )


def route(route_id, coords, *, eta=20.0, km=15.0) -> RouteCandidate:
    return RouteCandidate(
        route_id=route_id,
        geometry={"type": "LineString", "coordinates": coords},
        distance_km=km,
        eta_min=eta,
        record=rec(SourceId.OSRM, ttl=900),
    )


def closure(coords, *, hard=True, simulated=False, road="W Francis Ave") -> Closure:
    return Closure(
        closure_id=f"c:{road}",
        description=f"{road} closed",
        road=road,
        geometry={"type": "LineString", "coordinates": coords},
        is_hard_closure=hard,
        simulated=simulated,
        record=rec(SourceId.WSDOT, ttl=1800),
    )


def ok(source) -> SourceStatus:
    return SourceStatus(source_id=source, outcome="OK", record_count=1)


# Safe corridor well south and east of the test perimeter.
SAFE_COORDS = [[-117.49, 47.72], [-117.45, 47.68], [-117.35, 47.66], [-117.28, 47.655]]
# Runs straight through the perimeter box.
THROUGH_FIRE_COORDS = [[-117.49, 47.72], [-117.57, 47.76], [-117.60, 47.78]]


# --- Gate 1: Level 3 ---------------------------------------------------------


class TestLevel3:
    def test_level_3_leads_with_leave_now_and_is_urgent(self):
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(pets=True, mobility=True),
            zone=zone(EvacLevel.LEVEL_3),
            zones=[zone(EvacLevel.LEVEL_3)],
            incidents=[incident()],
            shelters=[shelter("Fairgrounds", ["pets", "mobility", "medical"])],
            routes=[route("route-A", SAFE_COORDS)],
            sources=[ok(SourceId.SREC), ok(SourceId.WFIGS)],
        )
        v, *_ = decide(ctx)
        assert v.urgent is True
        assert v.level is EvacLevel.LEVEL_3
        assert v.recommended_action.startswith("LEAVE NOW")

    def test_level_3_still_runs_the_constraint_search(self):
        """Urgency must not skip the hard-constraint filter (DESIGN 7.1)."""
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(pets=True, mobility=True),
            zone=zone(EvacLevel.LEVEL_3),
            zones=[zone(EvacLevel.LEVEL_3)],
            incidents=[incident()],
            shelters=[
                shelter("No Pets Site", ["mobility", "medical"], distance_km=2.0),
                shelter("Fairgrounds", ["pets", "mobility"], distance_km=18.0),
            ],
            routes=[route("route-A", SAFE_COORDS)],
            sources=[ok(SourceId.SREC), ok(SourceId.WFIGS)],
        )
        v, _, _, eligible, rejected = decide(ctx)
        assert v.urgent is True
        assert [s.name for s in eligible] == ["Fairgrounds"]
        assert rejected[0][0].name == "No Pets Site"


# --- Gate 2: hard constraints ------------------------------------------------


class TestHardConstraints:
    def test_constraints_filter_before_distance_ranking(self):
        needs = HouseholdNeeds(pets=True, mobility=True)
        shelters = [
            shelter("Close but no pets", ["mobility", "medical"], distance_km=1.0),
            shelter("Close but no ADA", ["pets"], distance_km=2.0),
            shelter("Far but complete", ["pets", "mobility", "medical"], distance_km=25.0),
        ]
        eligible, rejected = filter_shelters(shelters, needs)
        assert [s.name for s in eligible] == ["Far but complete"]
        assert len(rejected) == 2

    def test_negative_control_distance_never_overrides_a_hard_need(self):
        """The nearest shelter must lose when it fails one hard requirement."""
        needs = HouseholdNeeds(mobility=True)
        eligible, rejected = filter_shelters(
            [
                shelter("Next door", ["pets", "medical"], distance_km=0.2),
                shelter("Across town", ["mobility"], distance_km=40.0),
            ],
            needs,
        )
        assert [s.name for s in eligible] == ["Across town"]
        assert "mobility" in rejected[0][1]

    def test_unknown_capability_is_not_a_capability(self):
        """Silence from the source must not be read as consent (DESIGN 2.2)."""
        eligible, rejected = filter_shelters(
            [shelter("Says nothing", [], distance_km=1.0)],
            HouseholdNeeds(medical=True),
        )
        assert eligible == []
        assert rejected[0][1] == ["medical"]

    def test_household_with_no_constraints_accepts_the_nearest(self):
        eligible, _ = filter_shelters(
            [
                shelter("Far", ["pets"], distance_km=30.0),
                shelter("Near", [], distance_km=1.0),
            ],
            HouseholdNeeds(),
        )
        assert eligible[0].name == "Near"


# --- Gate 3: route validation ------------------------------------------------


class TestRouteValidation:
    def test_clear_route_is_approved(self):
        ctx = HazardContext(*ORIGIN, needs=HouseholdNeeds(), incidents=[incident()])
        r = validate_route(route("route-A", SAFE_COORDS), ctx)
        assert r.approved is True
        assert r.rejection_reason is None

    def test_negative_control_route_through_a_perimeter_is_rejected(self):
        ctx = HazardContext(*ORIGIN, needs=HouseholdNeeds(), incidents=[incident()])
        r = validate_route(route("route-B", THROUGH_FIRE_COORDS), ctx)
        assert r.approved is False
        assert "perimeter" in r.rejection_reason

    def test_negative_control_route_through_a_hard_closure_is_rejected(self):
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            closures=[closure([[-117.47, 47.70], [-117.43, 47.70]])],
        )
        r = validate_route(
            route("route-A", [[-117.49, 47.72], [-117.45, 47.699], [-117.40, 47.68]]),
            ctx,
        )
        assert r.approved is False
        assert "closure" in r.rejection_reason

    def test_a_soft_alert_does_not_reject_a_route(self):
        """Only hard closures reject. A lane-restriction alert is not a wall."""
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            closures=[closure([[-117.47, 47.70], [-117.43, 47.70]], hard=False)],
        )
        r = validate_route(
            route("route-A", [[-117.49, 47.72], [-117.45, 47.699], [-117.40, 47.68]]),
            ctx,
        )
        assert r.approved is True

    def test_leaving_a_level_3_zone_is_allowed(self):
        """The origin is inside the Level 3 zone; driving out is the goal."""
        ctx = HazardContext(
            *ORIGIN, needs=HouseholdNeeds(), zones=[zone(EvacLevel.LEVEL_3)]
        )
        r = validate_route("route-A" and route("route-A", SAFE_COORDS), ctx)
        assert r.approved is True

    def test_negative_control_no_survivors_is_stated_not_softened(self):
        """When everything is rejected the answer must say so, not pick least-bad."""
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=zone(EvacLevel.LEVEL_3),
            zones=[zone(EvacLevel.LEVEL_3)],
            incidents=[incident()],
            shelters=[shelter("Fairgrounds", [])],
            routes=[
                route("route-A", THROUGH_FIRE_COORDS),
                route("route-B", THROUGH_FIRE_COORDS),
            ],
            sources=[ok(SourceId.SREC), ok(SourceId.WFIGS)],
        )
        v, approved, rejected, _, _ = decide(ctx)
        assert approved == []
        assert len(rejected) == 2
        assert v.route_summary is None
        assert any("rejected as unsafe" in w for w in v.critical_warnings)

    def test_ranking_prefers_hazard_margin_over_eta(self):
        ctx = HazardContext(*ORIGIN, needs=HouseholdNeeds(), incidents=[incident()])
        fast_close = route("route-fast", [[-117.51, 47.73], [-117.45, 47.70]], eta=10.0)
        slow_far = route("route-slow", SAFE_COORDS, eta=45.0)
        ctx.routes = [fast_close, slow_far]
        from app.safety import validate_all

        approved, _ = validate_all(ctx)
        assert approved[0].route_id == "route-slow"


# --- Gate 4: re-entry --------------------------------------------------------


class TestReEntry:
    def test_negative_control_downgrade_is_not_an_all_clear(self):
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=zone(EvacLevel.LEVEL_1),
            zones=[zone(EvacLevel.LEVEL_1)],
            hazmat_cleared=None,
        )
        allowed, blockers = can_return_home(ctx)
        assert allowed is False
        assert any("hazmat" in b or "clearance" in b for b in blockers)

    def test_negative_control_absent_zone_is_not_an_all_clear(self):
        ctx = HazardContext(*ORIGIN, needs=HouseholdNeeds(), zone=None, zones=[])
        allowed, blockers = can_return_home(ctx)
        assert allowed is False
        assert any("absence of a zone" in b for b in blockers)

    def test_explicit_clearance_with_no_zone_and_no_fire_permits_return(self):
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=zone(EvacLevel.NONE),
            zones=[zone(EvacLevel.NONE)],
            hazmat_cleared=True,
            incidents=[],
        )
        allowed, blockers = can_return_home(ctx)
        assert allowed is True, blockers

    def test_nearby_fire_blocks_return_even_with_clearance(self):
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=zone(EvacLevel.NONE),
            zones=[zone(EvacLevel.NONE)],
            hazmat_cleared=True,
            incidents=[incident(distance_km=2.0)],
        )
        allowed, _ = can_return_home(ctx)
        assert allowed is False

    def test_verdict_carries_the_refusal(self):
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=zone(EvacLevel.LEVEL_1),
            zones=[zone(EvacLevel.LEVEL_1)],
            sources=[ok(SourceId.SREC), ok(SourceId.WFIGS)],
        )
        v, *_ = decide(ctx)
        assert v.can_return_home is False
        assert any("Do not return home yet" in w for w in v.critical_warnings)


# --- Gate 5: staleness -------------------------------------------------------


class TestStaleness:
    def test_stale_record_is_flagged(self):
        z = zone(EvacLevel.LEVEL_3, age_s=7200, ttl=3600)
        assert z.record.stale is True

    def test_negative_control_stale_record_cannot_lower_the_level(self):
        """A stale NONE beside a fresh Level 3 must not produce a downgrade."""
        fresh3 = zone(EvacLevel.LEVEL_3)
        stale_none = zone(EvacLevel.NONE, age_s=99999, ttl=3600)
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=fresh3,
            zones=[fresh3, stale_none],
            incidents=[incident()],
            sources=[ok(SourceId.SREC), ok(SourceId.WFIGS)],
        )
        v, *_ = decide(ctx)
        assert v.level is EvacLevel.LEVEL_3

    def test_negative_control_a_stale_all_clear_becomes_unverified(self):
        """The gate that matters: age must never be reported as calm.

        A zone record past its freshness window saying "no active evacuation"
        means nobody has published an update, not that the area is safe.
        """
        stale_none = zone(EvacLevel.NONE, age_s=99999, ttl=3600)
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=stale_none,
            zones=[stale_none],
            incidents=[],
            sources=[ok(SourceId.SREC), ok(SourceId.WFIGS)],
        )
        v, *_ = decide(ctx)
        assert v.level is EvacLevel.UNKNOWN, "a stale NONE must not read as clear"
        assert any("cannot be used to tell you the area is clear" in w for w in v.critical_warnings)

    def test_negative_control_a_stale_level_1_does_not_stay_level_1(self):
        stale_l1 = zone(EvacLevel.LEVEL_1, age_s=99999, ttl=3600)
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=stale_l1,
            zones=[stale_l1],
            sources=[ok(SourceId.SREC), ok(SourceId.WFIGS)],
        )
        v, *_ = decide(ctx)
        assert v.level is EvacLevel.UNKNOWN

    def test_a_stale_level_3_is_not_downgraded(self):
        """Staleness degrades reassurance, never urgency."""
        stale_l3 = zone(EvacLevel.LEVEL_3, age_s=99999, ttl=3600)
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=stale_l3,
            zones=[stale_l3],
            incidents=[incident()],
            sources=[ok(SourceId.SREC), ok(SourceId.WFIGS)],
        )
        v, *_ = decide(ctx)
        assert v.level is EvacLevel.LEVEL_3
        assert v.urgent is True

    def test_a_fresh_none_is_reported_as_none(self):
        """The gate must not fire on fresh data, or it reports nothing usable."""
        fresh_none = zone(EvacLevel.NONE)
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=fresh_none,
            zones=[fresh_none],
            incidents=[],
            sources=[ok(SourceId.SREC), ok(SourceId.WFIGS)],
        )
        v, *_ = decide(ctx)
        assert v.level is EvacLevel.NONE

    def test_staleness_is_reported_to_the_reader(self):
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=zone(EvacLevel.LEVEL_2),
            zones=[zone(EvacLevel.LEVEL_2)],
            incidents=[incident(age_s=200000, ttl=86400)],
            sources=[ok(SourceId.SREC), ok(SourceId.WFIGS)],
        )
        v, *_ = decide(ctx)
        assert any("Stale data" in u for u in v.unverified)


# --- Gate 6: coverage honesty ------------------------------------------------


class TestCoverageHonesty:
    def test_policy_denial_is_named_as_a_gap(self):
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            sources=[
                ok(SourceId.SREC),
                SourceStatus(
                    source_id=SourceId.WSDOT,
                    outcome="POLICY_DENIED",
                    detail="host not allowed",
                ),
            ],
        )
        gaps = coverage_gaps(ctx)
        assert any("blocked by the sandbox egress policy" in g for g in gaps)

    def test_negative_control_upstream_error_is_never_no_hazard(self):
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            sources=[
                ok(SourceId.SREC),
                SourceStatus(source_id=SourceId.WFIGS, outcome="UPSTREAM_ERROR", detail="500"),
            ],
        )
        gaps = coverage_gaps(ctx)
        assert any("not evidence that there is no hazard" in g for g in gaps)

    def test_blocked_action_appears_in_the_verdict(self):
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=zone(EvacLevel.LEVEL_2),
            zones=[zone(EvacLevel.LEVEL_2)],
            sources=[ok(SourceId.SREC), ok(SourceId.WFIGS)],
            blocked=[
                BlockedAction(
                    tool="get_fire_camera",
                    host="cameras.alertwildfire.org",
                    policy="spokane_evac",
                    detail="not an allowed host",
                )
            ],
        )
        v, *_ = decide(ctx)
        assert any("get_fire_camera was blocked by policy" in u for u in v.unverified)


# --- Cross-source consensus --------------------------------------------------


class TestConsensus:
    def test_agreement_gives_high_confidence(self):
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=zone(EvacLevel.LEVEL_3),
            zones=[zone(EvacLevel.LEVEL_3)],
            incidents=[incident(distance_km=4.0)],
            sources=[ok(SourceId.SREC), ok(SourceId.WFIGS)],
        )
        c = evaluate_consensus(ctx)
        assert c.agreed is True
        assert c.confidence == "high"

    def test_negative_control_fire_next_door_with_no_zone_is_a_conflict(self):
        """The disagreement a single-source lookup would never surface."""
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=None,
            zones=[],
            incidents=[incident(distance_km=2.0)],
            sources=[ok(SourceId.SREC), ok(SourceId.WFIGS)],
        )
        c = evaluate_consensus(ctx)
        assert c.agreed is False
        assert c.confidence == "low"
        assert c.level is EvacLevel.LEVEL_2, "conservative reading must win"
        assert any("not an all-clear" in x for x in c.conflicts)

    def test_single_source_is_never_high_confidence(self):
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=zone(EvacLevel.LEVEL_3),
            zones=[zone(EvacLevel.LEVEL_3)],
            sources=[ok(SourceId.SREC)],
        )
        c = evaluate_consensus(ctx)
        assert c.confidence == "low"
        assert c.agreed is False

    def test_negative_control_escalation_survives_losing_a_source(self):
        """Losing SREC must not turn a fire on the doorstep into "Unknown".

        A transient failure of the zone source makes us less certain the area is
        safe, not more. If the escalation only fired when every source answered,
        it would fail in exactly the conditions that need it.
        """
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=None,
            zones=[],
            incidents=[incident(distance_km=0.0)],
            sources=[
                ok(SourceId.WFIGS),
                SourceStatus(
                    source_id=SourceId.SREC, outcome="UPSTREAM_ERROR", detail="timeout"
                ),
            ],
        )
        c = evaluate_consensus(ctx)
        assert c.level is EvacLevel.LEVEL_2, "must escalate on one source alone"
        assert c.confidence == "low"

        v, *_ = decide(ctx)
        assert v.level is EvacLevel.LEVEL_2
        assert v.headline.startswith("Level 2")

    def test_a_distant_fire_with_no_zone_does_not_escalate(self):
        """The escalation must be bounded, or every answer becomes Level 2."""
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=None,
            zones=[],
            incidents=[incident(distance_km=60.0)],
            sources=[ok(SourceId.SREC), ok(SourceId.WFIGS)],
        )
        c = evaluate_consensus(ctx)
        assert c.level is not EvacLevel.LEVEL_2

    def test_being_inside_a_perimeter_is_phrased_as_such(self):
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=None,
            zones=[],
            incidents=[incident(name="OLD TRAILS", distance_km=0.0)],
            sources=[ok(SourceId.SREC), ok(SourceId.WFIGS)],
        )
        c = evaluate_consensus(ctx)
        assert any("inside the mapped perimeter" in x for x in c.conflicts), c.conflicts

    def test_unknown_level_never_reports_high_confidence(self):
        ctx = HazardContext(
            *ORIGIN,
            needs=HouseholdNeeds(),
            zone=None,
            zones=[],
            incidents=[],
            sources=[ok(SourceId.SREC), ok(SourceId.WFIGS)],
        )
        c = evaluate_consensus(ctx)
        assert c.confidence in ("low", "medium")


# --- EvacLevel ordering ------------------------------------------------------


class TestEvacLevelOrdering:
    def test_unknown_never_wins_a_conservative_max_against_a_real_level(self):
        assert max(EvacLevel.UNKNOWN, EvacLevel.LEVEL_1) is EvacLevel.LEVEL_1
        assert max(EvacLevel.UNKNOWN, EvacLevel.NONE) is EvacLevel.NONE

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Level 3", EvacLevel.LEVEL_3),
            ("GO", EvacLevel.LEVEL_3),
            ("Level 2", EvacLevel.LEVEL_2),
            ("SET", EvacLevel.LEVEL_2),
            ("READY", EvacLevel.LEVEL_1),
            ("Normal", EvacLevel.NONE),
            (None, EvacLevel.UNKNOWN),
            ("", EvacLevel.UNKNOWN),
            ("something unparseable", EvacLevel.UNKNOWN),
        ],
    )
    def test_parsing(self, raw, expected):
        assert EvacLevel.parse(raw) is expected
