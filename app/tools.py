"""Tool implementations and the schemas the model sees.

Each tool is thin: it calls a source, normalises the result into the session,
records a source status and a trace step, and returns a compact JSON-safe dict
for the model. Tools never decide safety. `plan_safe_route` in particular
returns candidates with `approved: null` — only `validate_route` may set it, and
only via `app/safety.py`.

Tool names match the book's contracts (section 4) so the skill prompts drop in
unchanged.
"""

from __future__ import annotations

import time
from typing import Any

from app.egress import EgressResult, Outcome
from app.models import (
    BlockedAction,
    Record,
    SourceId,
    SourceStatus,
    Shelter,
    StepKind,
)
from app.safety import HazardContext, can_return_home, filter_shelters, validate_all
from app.session import EvacuationSession
from app.config import settings
from app import replay
from app.sources import firecam, mapbox, nominatim, osrm, srec, wfigs, wsdot

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "geocode",
            "description": (
                "Resolve a landmark, road name or address in the Spokane area to "
                "coordinates. Call this first if you only have a place name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Landmark or address"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_evacuation_status",
            "description": (
                "Official evacuation level for the user's location, cross-checked "
                "against active fire geometry. Returns sources, consensus and "
                "freshness. Always call this before advising anyone to stay or go."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_active_incidents",
            "description": (
                "Active wildfire incidents near the user, with distance, size, "
                "containment and observation time. Containment percentage is never "
                "a safety guarantee."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "radius_km": {"type": "number", "description": "Search radius, default 80"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_shelters",
            "description": (
                "Shelters that satisfy every hard household requirement. Hard "
                "constraints are applied before distance ranking, so the nearest "
                "shelter is often not the answer."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_closures",
            "description": "Known road closures and highway alerts near the user.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_safe_route",
            "description": (
                "Generate candidate evacuation routes to a shelter. This does NOT "
                "decide safety — every candidate comes back unapproved and must be "
                "passed to validate_route."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "shelter_id": {
                        "type": "string",
                        "description": "Shelter to route to; defaults to the best eligible one",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_route",
            "description": (
                "Safety gate for candidate routes. Rejects any route intersecting a "
                "hard closure, a fire perimeter plus buffer, or a Level 3 zone, then "
                "ranks survivors by hazard margin. A route may only be recommended "
                "after this approves it."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_hazmat_clearance",
            "description": (
                "Whether an explicit re-entry or hazmat clearance has been issued "
                "for the user's address. Call this before answering any question "
                "about going home. A downgrade is not a clearance."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fire_camera",
            "description": (
                "Pull the nearest fire-camera still for visual confirmation of fire "
                "position. Useful when perimeter data is stale or sources disagree."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_notification",
            "description": (
                "Send the evacuation plan to a contact. The recipient must already "
                "have been approved by the user in this session."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact": {"type": "string", "description": "Recipient"},
                    "message": {"type": "string", "description": "Short SMS-style message"},
                },
                "required": ["contact"],
            },
        },
    },
]


def _status(source: SourceId, result: EgressResult, count: int = 0, stale: int = 0) -> SourceStatus:
    return SourceStatus(
        source_id=source,
        outcome=result.outcome.value,
        detail=(result.denial.summary if result.denial else result.error),
        record_count=count,
        stale_count=stale,
    )


def _stale_count(records: list[Record]) -> int:
    return sum(1 for r in records if r.stale)


def _ctx(session: EvacuationSession) -> HazardContext:
    return HazardContext(
        lat=session.place.lat if session.place else 0.0,
        lon=session.place.lon if session.place else 0.0,
        needs=session.needs,
        zone=session.zone,
        zones=session.zones,
        incidents=session.incidents,
        shelters=session.shelters,
        closures=session.closures,
        routes=session.routes,
        sources=session.sources,
        blocked=session.blocked,
        hazmat_cleared=session.hazmat_cleared,
        hazmat_note=session.hazmat_note,
    )


# --- tools -------------------------------------------------------------------


async def geocode(session: EvacuationSession, query: str | None = None) -> dict[str, Any]:
    q = (query or session.query or "").strip()
    if not q:
        return {"error": "no location given"}

    # Keep the authored demo internally consistent: its closure geometry and
    # captured route alternatives were built from the replayed Rifle Club
    # origin. Every other address remains live and is never snapped there.
    scenario_replay = settings.replay and replay.is_scenario_query(q)
    use_mapbox = (
        settings.location_provider.strip().lower() == "mapbox"
        and not scenario_replay
    )
    if use_mapbox:
        place, result = await mapbox.geocode(q)
        source_id = SourceId.MAPBOX
    else:
        place, result = await nominatim.geocode(q)
        source_id = SourceId.NOMINATIM
    session.record_source(_status(source_id, result, 1 if place else 0))

    if place is None:
        return {
            "error": "could not resolve that location",
            "outcome": result.outcome.value,
            "detail": result.error,
        }

    session.place = place
    return {
        "lat": place.lat,
        "lon": place.lon,
        "label": place.label,
        "source": source_id.value,
        "data_class": "replay" if result.outcome is Outcome.REPLAY else "official",
    }


async def get_evacuation_status(session: EvacuationSession) -> dict[str, Any]:
    if session.place is None:
        await geocode(session)
    if session.place is None:
        return {"error": "location unknown"}

    lat, lon = session.place.lat, session.place.lon

    zones, zres = await srec.get_evacuation_zones(lat, lon)
    session.zones = zones
    session.zone = srec.zone_containing(lat, lon, zones)
    session.record_source(
        _status(SourceId.SREC, zres, len(zones), _stale_count([z.record for z in zones]))
    )

    # The cross-check needs incident geometry, so make sure we have it.
    if not session.incidents:
        await get_active_incidents(session)

    from app.safety import evaluate_consensus

    consensus = evaluate_consensus(_ctx(session))
    session.consensus = consensus

    return {
        "level": consensus.level.label,
        "zone_name": session.zone.boundary_desc if session.zone else None,
        "plain_language_summary": (
            session.zone.public_message
            if session.zone and session.zone.public_message
            else consensus.explanation
        ),
        "sources_checked": [s.value for s in consensus.sources_checked],
        "consensus": consensus.agreed,
        "confidence": consensus.confidence,
        "conflicts": consensus.conflicts,
        "updated_at": (
            session.zone.record.as_of if session.zone else "no zone record for this point"
        ),
        "note": (
            "No evacuation zone is published for this point. That is not an all-clear."
            if session.zone is None
            else None
        ),
    }


async def get_active_incidents(
    session: EvacuationSession, radius_km: float = 80.0
) -> dict[str, Any]:
    if session.place is None:
        return {"error": "location unknown"}

    incidents, ires, pres = await wfigs.get_active_incidents(
        session.place.lat, session.place.lon, radius_km=radius_km
    )
    session.incidents = incidents

    # Report the worse of the two layers: a missing perimeter layer materially
    # weakens route validation even when the point layer succeeded.
    worse = ires if ires.outcome is not Outcome.OK else pres
    session.record_source(
        _status(
            SourceId.WFIGS,
            worse,
            len(incidents),
            _stale_count([i.record for i in incidents]),
        )
    )

    return {
        "count": len(incidents),
        "incidents": [
            {
                "name": i.name,
                "distance_km": round(i.distance_km, 1) if i.distance_km is not None else None,
                "acres": i.acres,
                "containment_pct": i.containment_pct,
                "has_perimeter": i.perimeter is not None,
                "stale": i.record.stale,
                "updated_at": i.record.as_of,
                "source": "WFIGS",
            }
            for i in incidents[:8]
        ],
        "note": "Containment percentage is not a statement about route safety.",
    }


async def find_shelters(session: EvacuationSession) -> dict[str, Any]:
    if session.place is None:
        return {"error": "location unknown"}

    lat, lon = session.place.lat, session.place.lon

    # Activated evacuation shelters first; the standing facility list is the
    # fallback and is explicitly not the same thing.
    poi, pres = await srec.get_evacuation_poi(lat, lon)
    candidates: list[Shelter] = list(poi)
    used_fallback = False

    if not candidates:
        facilities, fres = await srec.get_facilities(lat, lon)
        candidates = facilities
        used_fallback = True
        pres = fres

    session.shelters = candidates
    session.record_source(
        _status(SourceId.SREC, pres, len(candidates), _stale_count([s.record for s in candidates]))
    )

    eligible, rejected = filter_shelters(candidates, session.needs)
    session.rejected_shelters = rejected
    if eligible:
        session.destination = eligible[0]

    return {
        "needs_applied": session.needs.hard_constraints,
        "preferred_shelter": _shelter_dict(eligible[0]) if eligible else None,
        "alternatives": [_shelter_dict(s) for s in eligible[1:3]],
        "rejected": [
            {"name": s.name, "distance_km": round(s.distance_km or 0, 1), "missing": u}
            for s, u in rejected[:5]
        ],
        "used_standing_facility_list": used_fallback,
        "note": (
            "No evacuation shelters are currently activated; these are standing "
            "emergency facilities and are not staffed shelters."
            if used_fallback
            else None
        ),
        "freshness": (
            candidates[0].record.as_of if candidates else "no shelter records returned"
        ),
    }


def _shelter_dict(s: Shelter) -> dict[str, Any]:
    return {
        "shelter_id": s.shelter_id,
        "name": s.name,
        "address": s.address,
        "distance_km": round(s.distance_km, 1) if s.distance_km is not None else None,
        "accepts": s.capabilities,
        "capacity_status": s.capacity_status or "not published",
        "capacity_known": s.capacity_known,
        "updated_at": s.record.as_of,
    }


async def get_closures(session: EvacuationSession) -> dict[str, Any]:
    if session.place is None:
        return {"error": "location unknown"}

    lat, lon = session.place.lat, session.place.lon

    local, lres = await srec.get_local_closures(lat, lon)
    state, ares, tres = await wsdot.get_closures(lat, lon)

    # The monitor swaps in the post-trigger fixture; mark anything it added.
    for c in local:
        if "SIMULATED" in (c.description or "").upper():
            c.simulated = True

    session.closures = local + state
    session.record_source(_status(SourceId.WSDOT, ares, len(state)))
    session.record_source(_status(SourceId.WSDOT_EOC, tres, 0))

    hard = [c for c in session.closures if c.is_hard_closure]
    return {
        "count": len(session.closures),
        "hard_closures": [
            {
                "road": c.road,
                "description": c.description,
                "simulated": c.simulated,
                "source": c.record.source_id.value,
                "updated_at": c.record.as_of,
            }
            for c in hard
        ],
        "alerts": len(session.closures) - len(hard),
        "eoc_feed": tres.outcome.value,
    }


async def plan_safe_route(
    session: EvacuationSession, shelter_id: str | None = None
) -> dict[str, Any]:
    if session.place is None:
        return {"error": "location unknown"}

    if not session.shelters:
        await find_shelters(session)

    target = None
    if shelter_id:
        target = next((s for s in session.shelters if s.shelter_id == shelter_id), None)
    if target is None:
        target = session.destination
    if target is None:
        eligible, _ = filter_shelters(session.shelters, session.needs)
        target = eligible[0] if eligible else None
    if target is None:
        return {
            "error": "no shelter meets this household's hard requirements, so there "
            "is nowhere safe to route to",
            "candidates": [],
        }

    session.destination = target

    scenario_replay = settings.replay and replay.is_scenario_query(session.query)
    use_mapbox = settings.route_provider.strip().lower() == "mapbox"
    if use_mapbox:
        routes, rres = await mapbox.plan_routes(
            (session.place.lat, session.place.lon), (target.lat, target.lon)
        )
        route_source = SourceId.MAPBOX
    else:
        routes, rres = await osrm.plan_routes(
            (session.place.lat, session.place.lon),
            (target.lat, target.lon),
            bypass_replay=(
                settings.replay
                and settings.live_location_in_replay
                and not scenario_replay
            ),
        )
        route_source = SourceId.OSRM
    session.routes = routes
    session.record_source(_status(route_source, rres, len(routes)))

    return {
        "destination": target.name,
        "candidates": [
            {
                "route_id": r.route_id,
                "distance_km": r.distance_km,
                "eta_min": r.eta_min,
                "approved": None,
            }
            for r in routes
        ],
        "note": "Candidates only. None of these is safe until validate_route approves it.",
    }


async def validate_route(session: EvacuationSession) -> dict[str, Any]:
    if not session.routes:
        return {"error": "no candidate routes to validate; call plan_safe_route first"}

    # Validation is only as good as the hazard data behind it. If the model
    # reached this tool without loading closures or incidents, an empty hazard
    # set would approve everything — a validator that has nothing to check
    # against returns "safe", which is the most dangerous possible default.
    # Load them here rather than trusting call order.
    if not session.closures:
        await get_closures(session)
    if not session.incidents:
        await get_active_incidents(session)

    ctx = _ctx(session)
    approved, rejected = validate_all(ctx)
    session.approved_routes = approved
    session.rejected_routes = rejected
    session.current_route = approved[0] if approved else None

    return {
        "selected_route_id": approved[0].route_id if approved else None,
        "approved": bool(approved),
        "rejected_routes": [
            {"route_id": r.route_id, "reason": r.rejection_reason} for r in rejected
        ],
        "warnings": (
            []
            if approved
            else [
                "Every candidate route was rejected. Do not improvise a way around "
                "this — advise calling 911 for evacuation assistance."
            ]
        ),
        "evidence": [
            {
                "route_id": r.route_id,
                "hazard_margin_km": (
                    round(r.hazard_margin_km, 2) if r.hazard_margin_km is not None else None
                ),
                "intersects": r.intersects,
            }
            for r in session.routes
        ],
        "freshness_summary": (
            f"{len(session.closures)} closure records, "
            f"{len(session.incidents)} incident records"
        ),
    }


async def check_hazmat_clearance(session: EvacuationSession) -> dict[str, Any]:
    """No clearance authority is in the allowlist, so the answer is always unknown.

    That is the honest result, and the guard treats unknown exactly as it treats
    "not cleared" — no return recommendation either way.
    """
    session.hazmat_cleared = None
    session.hazmat_note = (
        "No hazmat or utility clearance source is available to this agent. "
        "Clearance is therefore unknown, which is not the same as cleared."
    )

    allowed, blockers = can_return_home(_ctx(session))
    return {
        "cleared": None,
        "notes": session.hazmat_note,
        "source": None,
        "updated_at": None,
        "safe_to_return": allowed,
        "blockers": blockers,
        "rule": "An evacuation downgrade is not equivalent to safe re-entry.",
    }


async def get_fire_camera(session: EvacuationSession) -> dict[str, Any]:
    """Real capability, real host, real refusal. See app/sources/firecam.py."""
    if session.place is None:
        return {"error": "location unknown"}

    result = await firecam.get_fire_camera(session.place.lat, session.place.lon)
    blocked = firecam.to_blocked_action(result)

    if blocked is not None:
        session.blocked.append(blocked)
        session.record_source(_status(SourceId.FIRECAM, result))
        return {
            "blocked": True,
            "host": blocked.host,
            "policy": blocked.policy,
            "reason": blocked.detail,
            "layer": blocked.layer,
            "instruction": (
                "This capability is blocked by the sandbox egress policy. Do not "
                "attempt to reach it another way. Report it as unavailable and "
                "continue with the remaining tools."
            ),
        }

    session.record_source(_status(SourceId.FIRECAM, result))
    return {"blocked": False, "outcome": result.outcome.value}


async def send_notification(
    session: EvacuationSession, contact: str, message: str | None = None
) -> dict[str, Any]:
    """Authorisation check for recipients (book section 7).

    A contact must already be approved in this session. An unapproved or
    model-invented recipient is refused, and the refusal is not negotiable —
    the agent may not substitute, infer or reformat its way to a send.
    """
    contact = (contact or "").strip()
    if not contact:
        return {"status": "blocked", "reason": "no recipient given"}

    approved = {c.strip().lower() for c in session.approved_contacts}
    if contact.lower() not in approved:
        session.blocked.append(
            BlockedAction(
                tool="send_notification",
                host="notification-gateway",
                method="POST",
                path=f"/send/{contact}",
                policy="action-scope",
                detail=(
                    f"recipient {contact} was not approved in the current session"
                ),
                layer="action",
            )
        )
        return {
            "status": "blocked",
            "reason": f"recipient {contact} was not approved in the current session",
            "approved_contacts": session.approved_contacts,
            "instruction": (
                "Do not retry, do not substitute another recipient, and do not ask "
                "the tool again. Tell the user the action was blocked and why."
            ),
        }

    body = message or (
        session.verdict.recommended_action if session.verdict else "Evacuation update"
    )
    session.notifications.append(
        {"contact": contact, "message": body, "at": time.time(), "delivered": "simulated"}
    )
    return {
        "status": "sent",
        "contact": contact,
        "message": body,
        "note": "Delivery is simulated in this prototype; nothing left the machine.",
    }


# --- dispatch ----------------------------------------------------------------

_IMPLS = {
    "geocode": geocode,
    "get_evacuation_status": get_evacuation_status,
    "get_active_incidents": get_active_incidents,
    "find_shelters": find_shelters,
    "get_closures": get_closures,
    "plan_safe_route": plan_safe_route,
    "validate_route": validate_route,
    "check_hazmat_clearance": check_hazmat_clearance,
    "get_fire_camera": get_fire_camera,
    "send_notification": send_notification,
}


async def call(
    session: EvacuationSession, name: str, arguments: dict[str, Any]
) -> tuple[dict[str, Any], int]:
    """Run one tool, returning its result and how long it took."""
    impl = _IMPLS.get(name)
    if impl is None:
        return {"error": f"unknown tool {name}"}, 0

    started = time.monotonic()
    try:
        result = await impl(session, **(arguments or {}))
    except TypeError as exc:
        result = {"error": f"bad arguments for {name}: {exc}"}
    except Exception as exc:  # a tool failure must not end the evacuation
        result = {"error": f"{name} failed: {exc}"}
    return result, int((time.monotonic() - started) * 1000)
