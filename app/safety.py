"""The deterministic safety layer. This module owns the resident-facing verdict.

Nothing here calls the network or the model. Everything is a pure function over
records produced at M2, so every gate is testable in isolation and the same
inputs always yield the same decision.

The model may phrase what this module decides. It may not overturn it, and it
may not write any field of `Verdict`. That separation is the reason a language
model is safe to have in this loop at all.

The gates, from DESIGN section 7 and the book's route-validator contract:

1. Level 3 is immediate — but the constraint search still runs.
2. Hard constraints are hard, never traded against distance.
3. No route through hazard; if nothing survives, say so.
4. No re-entry without an explicit all-clear.
5. Stale data may be shown, never used to downgrade.
6. Coverage honesty — name what is missing and what it means.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.geo import buffer_km, clearance_km, intersects, to_geometry
from app.models import (
    BlockedAction,
    Closure,
    Consensus,
    EvacLevel,
    EvacZone,
    HouseholdNeeds,
    Incident,
    RouteCandidate,
    Shelter,
    SourceId,
    SourceStatus,
    Verdict,
)

# A route is rejected if it comes within this of a mapped perimeter. Fire moves,
# perimeters are hours old, and a road that merely touches the edge of yesterday's
# polygon is not a road anyone should be sent down.
PERIMETER_BUFFER_KM = 1.5

# How close a fire has to be, with no published evacuation zone, before the
# absence of a zone becomes a reportable conflict rather than a quiet gap.
UNZONED_PROXIMITY_KM = 8.0


@dataclass
class HazardContext:
    """Everything the gates need, gathered by the agent before they run."""

    lat: float
    lon: float
    needs: HouseholdNeeds
    zone: EvacZone | None = None
    zones: list[EvacZone] = field(default_factory=list)
    incidents: list[Incident] = field(default_factory=list)
    shelters: list[Shelter] = field(default_factory=list)
    closures: list[Closure] = field(default_factory=list)
    routes: list[RouteCandidate] = field(default_factory=list)
    sources: list[SourceStatus] = field(default_factory=list)
    blocked: list[BlockedAction] = field(default_factory=list)
    hazmat_cleared: bool | None = None
    hazmat_note: str | None = None


# --- Gate 6 groundwork: what did we actually manage to look at? --------------


def coverage_gaps(ctx: HazardContext) -> list[str]:
    """Layers that were blocked, errored, or never consulted.

    Phrased for a resident, and always in terms of what it means rather than
    which HTTP call failed.
    """
    gaps: list[str] = []
    seen = {s.source_id for s in ctx.sources}

    for s in ctx.sources:
        if s.usable:
            continue
        if s.outcome == "POLICY_DENIED":
            gaps.append(
                f"{s.source_id.value} was blocked by the sandbox egress policy, "
                "so that layer is missing from this answer."
            )
        elif s.outcome == "SANDBOX_UNAVAILABLE":
            gaps.append(
                f"{s.source_id.value} could not be reached because the sandbox is "
                "unavailable. Treat this answer as incomplete."
            )
        else:
            detail = f" ({s.detail})" if s.detail else ""
            gaps.append(
                f"{s.source_id.value} did not answer{detail}. "
                "This is not evidence that there is no hazard there."
            )

    if SourceId.SREC not in seen:
        gaps.append("No evacuation-zone source was consulted for this location.")
    if SourceId.WFIGS not in seen:
        gaps.append("No fire-perimeter source was consulted for this location.")

    return gaps


# --- Cross-source consensus (book section 4) --------------------------------


def evaluate_consensus(ctx: HazardContext) -> Consensus:
    """Cross-check the declared evacuation level against fire geometry.

    Two independent Tier-1 authorities: SREC says what the level *is*, WFIGS
    says where the fire *is*. When they disagree the conflict is reported and
    confidence drops — it is never resolved silently, and the conservative
    reading is the one that survives.
    """
    checked: list[SourceId] = []
    conflicts: list[str] = []

    srec_ok = any(s.source_id is SourceId.SREC and s.usable for s in ctx.sources)
    wfigs_ok = any(s.source_id is SourceId.WFIGS and s.usable for s in ctx.sources)
    if srec_ok:
        checked.append(SourceId.SREC)
    if wfigs_ok:
        checked.append(SourceId.WFIGS)

    declared = ctx.zone.level if ctx.zone else (EvacLevel.NONE if srec_ok else EvacLevel.UNKNOWN)

    nearest = min(
        (i for i in ctx.incidents if i.distance_km is not None),
        key=lambda i: i.distance_km,
        default=None,
    )

    # A fresh perimeter close by with no zone published is the disagreement that
    # matters most, and the one a single-source lookup would never surface.
    if wfigs_ok and nearest is not None and nearest.distance_km <= UNZONED_PROXIMITY_KM:
        if declared in (EvacLevel.NONE, EvacLevel.UNKNOWN):
            # A distance of zero means the point is *inside* the mapped
            # perimeter. "0.0 km away" is technically true and completely
            # useless to someone deciding whether to leave.
            where = (
                f"This address is inside the mapped perimeter of {nearest.name}"
                if nearest.distance_km < 0.05
                else f"{nearest.name} is {nearest.distance_km:.1f} km away"
            )
            conflicts.append(
                f"{where} ({nearest.record.as_of}), but no evacuation zone is "
                "published for this location. Absence of a zone is not an all-clear."
            )

    if ctx.zone is not None and ctx.zone.record.stale:
        conflicts.append(
            f"The evacuation zone record is older than its freshness window "
            f"({ctx.zone.record.as_of}); it cannot be used to lower the level."
        )

    if srec_ok and not ctx.zones:
        conflicts.append(
            "SREC returned no evacuation areas at all. Either none are published "
            "right now, or the layer is not being updated."
        )

    # The conservative reading, applied before any early return. An unzoned
    # point on top of a live fire is treated as at least Level 2 whether we
    # managed to reach one source or both — losing SREC makes us *less* certain
    # the area is safe, not more, and an escalation that only fires when every
    # source answered is an escalation that fails exactly when it is needed.
    level = declared
    if (
        declared in (EvacLevel.NONE, EvacLevel.UNKNOWN)
        and nearest is not None
        and nearest.distance_km <= UNZONED_PROXIMITY_KM
    ):
        level = EvacLevel.LEVEL_2

    if len(checked) < 2:
        explanation = (
            "Only one source could be checked, so this level is unconfirmed."
            if checked
            else "No evacuation source could be checked."
        )
        if level is not declared:
            explanation += (
                " An active fire is close enough that the area is treated as "
                "Level 2 until an authority says otherwise."
            )
        return Consensus(
            agreed=False,
            confidence="low",
            level=level,
            sources_checked=checked,
            conflicts=conflicts,
            explanation=explanation,
        )

    if conflicts:
        return Consensus(
            agreed=False,
            confidence="low",
            level=level,
            sources_checked=checked,
            conflicts=conflicts,
            explanation=(
                "Sources disagree. The more conservative reading is used and the "
                "conflict is shown rather than resolved."
            ),
        )

    return Consensus(
        agreed=True,
        confidence="high" if declared is not EvacLevel.UNKNOWN else "low",
        level=declared,
        sources_checked=checked,
        conflicts=[],
        explanation="Evacuation level and fire geometry agree.",
    )


# --- Gate 2: hard constraints ------------------------------------------------


def filter_shelters(
    shelters: list[Shelter], needs: HouseholdNeeds
) -> tuple[list[Shelter], list[tuple[Shelter, list[str]]]]:
    """Split shelters into eligible and rejected.

    Filtering happens *before* any distance ranking, and a shelter is never
    admitted because it is closer. The rejected list keeps its reasons so the
    UI can show why the nearest option was not chosen.
    """
    eligible: list[Shelter] = []
    rejected: list[tuple[Shelter, list[str]]] = []

    for s in shelters:
        unmet = s.unmet(needs)
        if unmet:
            rejected.append((s, unmet))
        else:
            eligible.append(s)

    eligible.sort(key=lambda s: s.distance_km if s.distance_km is not None else 1e9)
    return eligible, rejected


# --- Gate 3: route validation ------------------------------------------------


def validate_route(
    route: RouteCandidate,
    ctx: HazardContext,
    *,
    buffer_km_value: float = PERIMETER_BUFFER_KM,
) -> RouteCandidate:
    """Approve or reject a single candidate. The only place `approved` is set.

    Rejection is not a routing failure. A router that returns a path through a
    fire has done its job; refusing to hand that path to a resident is this
    function's job.
    """
    reasons: list[str] = []
    hit: list[str] = []

    geom = to_geometry(route.geometry)
    if geom is None:
        route.approved = False
        route.rejection_reason = "route geometry could not be read"
        return route

    # Hard closures
    for c in ctx.closures:
        if not c.is_hard_closure:
            continue
        cg = to_geometry(c.geometry)
        if cg is None:
            continue
        # Closure lines are drawn coarsely; a 150 m tolerance keeps a route from
        # threading a closure that it visually runs straight through.
        if intersects(geom, cg, buffer=0.15):
            tag = "simulated closure" if c.simulated else "closure"
            reasons.append(f"{tag}: {c.road or c.description}")
            hit.append(c.closure_id)

    # Fire perimeters, with buffer
    for inc in ctx.incidents:
        pg = to_geometry(inc.perimeter)
        if pg is None:
            continue
        if intersects(geom, pg, buffer=buffer_km_value):
            reasons.append(
                f"passes within {buffer_km_value:g} km of the {inc.name} perimeter"
            )
            hit.append(inc.incident_id)

    # Level 3 zones
    for z in ctx.zones:
        if z.level is not EvacLevel.LEVEL_3:
            continue
        zg = to_geometry(z.record.geometry)
        if zg is None:
            continue
        # The origin is usually inside the Level 3 zone — leaving it is the
        # point. Only a route that re-enters or runs along it is a problem, so
        # measure how much of the route lies inside rather than whether it
        # touches at all.
        try:
            inside = geom.intersection(zg).length
            share = inside / geom.length if geom.length else 0.0
        except Exception:
            share = 0.0
        if share > 0.35:
            reasons.append(
                f"{share:.0%} of this route stays inside the Level 3 zone "
                f"({z.boundary_desc or z.zone_id})"
            )
            hit.append(z.zone_id)

    route.intersects = hit
    route.hazard_margin_km = clearance_km(
        geom, [to_geometry(i.perimeter) for i in ctx.incidents if i.perimeter]
    )

    if reasons:
        route.approved = False
        route.rejection_reason = "; ".join(reasons)
    else:
        route.approved = True
        route.rejection_reason = None

    return route


def validate_all(ctx: HazardContext) -> tuple[list[RouteCandidate], list[RouteCandidate]]:
    """Validate every candidate, then rank the survivors.

    Ranking is hazard margin first, freshness second, ETA last — the book's
    order, and deliberately not the router's order.
    """
    for r in ctx.routes:
        validate_route(r, ctx)

    approved = [r for r in ctx.routes if r.approved]
    rejected = [r for r in ctx.routes if not r.approved]

    approved.sort(
        key=lambda r: (
            -(r.hazard_margin_km if r.hazard_margin_km is not None else 0.0),
            r.record.stale,
            r.eta_min,
        )
    )
    return approved, rejected


# --- Gate 4: re-entry --------------------------------------------------------


def can_return_home(ctx: HazardContext) -> tuple[bool, list[str]]:
    """Whether a re-entry recommendation is permissible. Almost always no.

    A downgrade is not an all-clear, an empty zone layer is not an all-clear,
    and unknown utility or hazmat status is not an all-clear.
    """
    blockers: list[str] = []

    level = ctx.zone.level if ctx.zone else EvacLevel.UNKNOWN
    if level in (EvacLevel.LEVEL_2, EvacLevel.LEVEL_3):
        blockers.append(f"the area is still at {level.label}")
    if level is EvacLevel.UNKNOWN:
        blockers.append(
            "no evacuation level could be confirmed for this address, and an "
            "unknown level is not an all-clear"
        )

    if ctx.zone is None and not ctx.zones:
        blockers.append(
            "no evacuation zone is published for this address; absence of a zone "
            "is not the same as an authority declaring it safe"
        )

    if ctx.hazmat_cleared is not True:
        blockers.append(
            ctx.hazmat_note
            or "no hazmat or utility clearance has been issued for this address"
        )

    if ctx.zone is not None and ctx.zone.record.stale:
        blockers.append("the evacuation record is stale and cannot justify a return")

    for inc in ctx.incidents:
        if inc.distance_km is not None and inc.distance_km <= 3.0:
            blockers.append(
                f"{inc.name} is still {inc.distance_km:.1f} km away "
                f"({inc.render_size()})"
            )
            break

    return (not blockers), blockers


# --- The verdict -------------------------------------------------------------


def decide(ctx: HazardContext) -> tuple[Verdict, list[RouteCandidate], list[RouteCandidate], list[Shelter], list[tuple[Shelter, list[str]]]]:
    """Produce the resident-facing verdict. Owned here, never by the model."""
    consensus = evaluate_consensus(ctx)
    gaps = coverage_gaps(ctx)

    eligible, rejected_shelters = filter_shelters(ctx.shelters, ctx.needs)
    approved_routes, rejected_routes = validate_all(ctx)

    # Take the most severe of the declared level and the cross-source reading.
    level = max(consensus.level, ctx.zone.level if ctx.zone else EvacLevel.UNKNOWN)

    warnings: list[str] = list(consensus.conflicts)
    unverified: list[str] = list(gaps)

    # Gate 5, the part that actually bites: a stale record may not be the basis
    # of a downgrade. A zone that has aged out of its freshness window and
    # reports NONE or Level 1 is not evidence the area is calm — it is evidence
    # that nobody has published an update. Reporting its reassuring value as
    # current is precisely the failure this gate exists to prevent, so the level
    # degrades to unverified instead. It can still be raised by a conflict
    # above; it can never be lowered by age.
    if (
        ctx.zone is not None
        and ctx.zone.record.stale
        and level in (EvacLevel.NONE, EvacLevel.LEVEL_1)
    ):
        level = EvacLevel.UNKNOWN
        warnings.append(
            f"The evacuation record for this address is past its freshness window "
            f"({ctx.zone.record.as_of}) and reports {ctx.zone.level.label}. "
            "A stale record cannot be used to tell you the area is clear, so the "
            "level is reported as unverified rather than low."
        )

    for b in ctx.blocked:
        unverified.append(
            f"{b.tool} was blocked by policy ({b.host}); that check did not happen."
        )

    # Gate 1: Level 3 leads with the instruction, and does not wait on routing.
    urgent = level is EvacLevel.LEVEL_3
    destination = eligible[0] if eligible else None
    route = approved_routes[0] if approved_routes else None

    if urgent:
        action = "LEAVE NOW."
        headline = "Level 3 — GO. Leave immediately."
    elif level is EvacLevel.LEVEL_2:
        action = "Be ready to leave immediately."
        headline = "Level 2 — SET. Be ready to go."
        if ctx.needs.hard_constraints:
            action = (
                "Leave now rather than waiting. With "
                f"{ctx.needs.describe()}, you need more time than most households."
            )
    elif level is EvacLevel.LEVEL_1:
        action = "Get ready. Pack and plan your route now."
        headline = "Level 1 — READY. Prepare to leave."
    elif level is EvacLevel.NONE:
        action = "No evacuation order applies to this address right now."
        headline = "No active evacuation zone."
    else:
        action = (
            "Evacuation status could not be confirmed. Treat this as unverified "
            "and check official channels."
        )
        headline = "Status unverified."

    # Shelter and route language, with the reason the obvious answer was not used.
    route_summary = None
    if destination is not None:
        if route is not None:
            margin = (
                f", staying {route.hazard_margin_km:.1f} km from the nearest perimeter"
                if route.hazard_margin_km is not None
                else ""
            )
            route_summary = (
                f"{route.route_id.upper()} — {route.distance_km:.1f} km, "
                f"about {route.eta_min:.0f} min{margin}."
            )
        elif ctx.routes:
            route_summary = None
            warnings.append(
                "Every candidate route was rejected as unsafe. "
                + "; ".join(
                    f"{r.route_id}: {r.rejection_reason}"
                    for r in rejected_routes
                    if r.rejection_reason
                )
                + ". Do not improvise around this — call 911 for evacuation assistance."
            )

    if not eligible and ctx.shelters:
        warnings.append(
            "No shelter meets every hard requirement for this household "
            f"({ctx.needs.describe()}). "
            + "; ".join(
                f"{s.name} lacks {', '.join(u)}" for s, u in rejected_shelters[:3]
            )
            + ". Call 211 or 911 for a placement that fits."
        )

    if destination is not None and not destination.capacity_known:
        unverified.append(
            f"{destination.name} does not publish live capacity; space is not confirmed."
        )

    # Gate 5, stated explicitly wherever a stale record is in play.
    stale_sources = sorted(
        {
            r.source_id.value
            for r in _all_records(ctx)
            if r.stale
        }
    )
    if stale_sources:
        unverified.append(
            "Stale data from " + ", ".join(stale_sources) + ". "
            "Shown for context; not used to lower any level or clear any road."
        )

    returnable, blockers = can_return_home(ctx)

    verdict = Verdict(
        recommended_action=action,
        headline=headline,
        level=level,
        urgent=urgent,
        destination=(
            f"{destination.name} — {destination.address}"
            if destination and destination.address
            else (destination.name if destination else None)
        ),
        route_summary=route_summary,
        critical_warnings=warnings,
        unverified=unverified,
        freshness_summary=_freshness(ctx, consensus),
        next_monitor_condition=(
            "Watching for new closures on the selected route, perimeter growth, "
            "and any change to the evacuation level for this address."
        ),
        can_return_home=returnable,
    )

    if not returnable:
        verdict.critical_warnings.append(
            "Do not return home yet: " + "; ".join(blockers) + "."
        )

    return verdict, approved_routes, rejected_routes, eligible, rejected_shelters


def _all_records(ctx: HazardContext):
    for z in ctx.zones:
        yield z.record
    for i in ctx.incidents:
        yield i.record
    for c in ctx.closures:
        yield c.record
    for s in ctx.shelters:
        yield s.record


def _freshness(ctx: HazardContext, consensus: Consensus) -> str:
    bits = []
    if ctx.zone is not None:
        bits.append(f"Evacuation level: {ctx.zone.record.as_of}")
    nearest = min(
        (i for i in ctx.incidents if i.distance_km is not None),
        key=lambda i: i.distance_km,
        default=None,
    )
    if nearest is not None:
        bits.append(f"Nearest fire: {nearest.record.as_of}")
    hard = [c for c in ctx.closures if c.is_hard_closure]
    if hard:
        bits.append(f"Closures: {hard[0].record.as_of}")
    bits.append(
        f"Cross-source: {len(consensus.sources_checked)} checked, "
        f"{'agree' if consensus.agreed else 'disagree'}, "
        f"confidence {consensus.confidence}"
    )
    return " · ".join(bits)
