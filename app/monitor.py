"""The always-on evacuation monitor.

After a route is selected, this keeps running. It re-fetches hazard state on an
interval, compares it against what the session already believed, and acts only
when something material changed. No user message is involved — that is the whole
point of the "always-on" claim, and it is why this is an agent rather than a
request handler.

The comparison is deliberately state-based rather than event-based. The monitor
does not get told "a closure appeared"; it fetches, diffs, and works out that
its own route is now invalid. That is the same code path a live feed would
exercise, so the demo trigger changes the world, not the agent's beliefs.

No-change rule (book, skill 7): if nothing material moved, no alert is raised.
A monitor that narrates every quiet poll trains people to ignore it.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from app import tools
from app.models import EvacLevel, StepKind, iso, utcnow
from app.session import EvacuationSession

# Fast enough to feel live on stage, slow enough not to hammer six public APIs.
POLL_SECONDS = 12.0


class MonitorState:
    """What the monitor compares against, captured at the end of each pass."""

    def __init__(self, session: EvacuationSession) -> None:
        # The consensus can conservatively raise an unzoned location when fire
        # geometry is close. Watching only the raw SREC zone would miss that
        # escalation, especially before a route has been selected.
        self.level = (
            session.consensus.level
            if session.consensus is not None
            else (session.zone.level if session.zone else EvacLevel.UNKNOWN)
        )
        self.closure_ids = {c.closure_id for c in session.closures if c.is_hard_closure}
        self.route_id = session.current_route.route_id if session.current_route else None
        self.incident_names = {i.name for i in session.incidents}
        self.destination = session.destination.shelter_id if session.destination else None


async def run_once(session: EvacuationSession) -> dict[str, Any] | None:
    """One monitoring pass. Returns an event dict if something material changed.

    The order matters: refresh the world, then decide whether our own plan is
    still valid, then replan only if it is not.
    """
    before = MonitorState(session)

    await tools.get_closures(session)
    await tools.get_active_incidents(session)
    await tools.get_evacuation_status(session)

    session.last_checked_at = utcnow()

    after = MonitorState(session)

    new_closures = after.closure_ids - before.closure_ids
    level_changed = after.level is not before.level
    new_incidents = after.incident_names - before.incident_names

    # Revalidate the selected route on every refresh, even when the identifiers
    # above are unchanged. Fire perimeters grow under the same incident name,
    # and authorities can revise the geometry of an existing closure without
    # assigning it a new ID. Treating identifiers as the whole hazard state
    # would leave an already-selected route approved after the hazard moved
    # across it.
    route_check: dict[str, Any] | None = None
    route_invalidated = False
    if session.routes and before.route_id is not None:
        route_check = await tools.validate_route(session)
        route_invalidated = not (
            session.current_route is not None
            and session.current_route.route_id == before.route_id
        )

    if not (new_closures or level_changed or new_incidents or route_invalidated):
        # No-change rule. Record the heartbeat, raise nothing.
        return None

    changes: list[str] = []
    simulated = False

    for cid in new_closures:
        c = next((x for x in session.closures if x.closure_id == cid), None)
        if c is None:
            continue
        simulated = simulated or c.simulated
        changes.append(f"new hard closure: {c.road or c.description}")

    if level_changed:
        changes.append(f"evacuation level changed to {after.level.label}")
    for name in new_incidents:
        changes.append(f"new incident nearby: {name}")
    if route_invalidated and not (new_closures or level_changed or new_incidents):
        changes.append("updated hazard geometry now intersects the selected route")

    session.step(
        StepKind.MONITOR,
        "Road-state change detected" if new_closures else "Hazard state changed",
        detail="; ".join(changes),
        outcome="changed",
        simulated=simulated,
    )

    event: dict[str, Any] = {
        "at": iso(session.last_checked_at),
        "changes": changes,
        "simulated": simulated,
        "replanned": False,
        "previous_route": before.route_id,
        "new_route": None,
        "notification": None,
    }

    # Does the change actually touch our plan? Revalidate before assuming so —
    # a closure on the other side of the county is news, not a reroute.
    if session.routes:
        result = route_check or await tools.validate_route(session)
        still_valid = (
            session.current_route is not None
            and before.route_id is not None
            and session.current_route.route_id == before.route_id
        )

        if not still_valid:
            rejected = next(
                (
                    r
                    for r in session.rejected_routes
                    if r.route_id == before.route_id
                ),
                None,
            )
            session.step(
                StepKind.GUARD,
                f"Current route {before.route_id} invalidated",
                detail=(rejected.rejection_reason if rejected else "no longer approved"),
                outcome="invalidated",
                simulated=simulated,
            )

            # Replan from scratch: new candidates to the same shelter, then
            # validate them. If nothing survives we say so rather than keeping a
            # dead route on screen.
            session.step(
                StepKind.MONITOR, "Replanning", outcome="replanning", simulated=simulated
            )
            await tools.plan_safe_route(session)
            result = await tools.validate_route(session)

            new_id = result.get("selected_route_id")
            event["replanned"] = True
            event["new_route"] = new_id

            if new_id:
                session.step(
                    StepKind.GUARD,
                    f"New route {new_id} approved",
                    detail=(
                        f"{session.current_route.distance_km:.1f} km, "
                        f"{session.current_route.eta_min:.0f} min"
                        if session.current_route
                        else None
                    ),
                    outcome="approved",
                    simulated=simulated,
                )
                event["notification"] = _notification(session, changes, simulated)
            else:
                session.step(
                    StepKind.GUARD,
                    "No safe route remains",
                    detail=(
                        "Every candidate was rejected. The resident is advised to "
                        "call 911 for evacuation assistance."
                    ),
                    outcome="no_route",
                    simulated=simulated,
                )
                event["notification"] = (
                    "URGENT: your evacuation route is blocked and no safe alternative "
                    "was found. Call 911 for evacuation assistance."
                )

    # Refresh the verdict so the page reflects the new world.
    _refresh_verdict(session)

    session.monitor_events.append(event)
    session.publish({"type": "monitor", "event": event, "state": session.snapshot()})
    session.persist()
    return event


def _notification(session: EvacuationSession, changes: list[str], simulated: bool) -> str:
    """Action first, reason second, route third, freshness last (book, skill 9)."""
    route = session.current_route
    dest = session.destination
    prefix = "[SIMULATED] " if simulated else ""
    parts = [
        f"{prefix}Your evacuation route has changed. Take "
        f"{route.route_id.upper() if route else 'the new route'} instead."
    ]
    if changes:
        parts.append(f"Reason: {changes[0]}.")
    if dest is not None:
        parts.append(f"Destination unchanged: {dest.name}.")
    if route is not None:
        parts.append(f"{route.distance_km:.0f} km, about {route.eta_min:.0f} min.")
    if session.last_checked_at:
        parts.append(f"Checked {iso(session.last_checked_at)}.")
    return " ".join(parts)


def _refresh_verdict(session: EvacuationSession) -> None:
    from app.safety import decide

    verdict, approved, rejected, eligible, rejected_shelters = decide(tools._ctx(session))
    session.approved_routes = approved
    session.rejected_routes = rejected
    session.rejected_shelters = rejected_shelters
    session.current_route = approved[0] if approved else None
    if eligible:
        session.destination = eligible[0]

    # Keep the model's prose from the original answer only while it is still
    # true. After a replan it describes a route that no longer exists.
    previous = session.verdict.narrative if session.verdict else None
    session.verdict = verdict
    if previous and not session.monitor_events:
        session.verdict.narrative = previous


class MonitorSupervisor:
    """One background task per monitored session."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    def start(self, session: EvacuationSession) -> None:
        if session.session_id in self._tasks:
            return
        session.monitoring = True
        session.step(
            StepKind.MONITOR,
            "Monitoring active",
            detail=(
                session.verdict.next_monitor_condition
                if session.verdict
                else "watching closures, perimeters and evacuation level"
            ),
            outcome="started",
        )
        self._tasks[session.session_id] = asyncio.create_task(self._loop(session))

    async def stop(self, session_id: str) -> None:
        task = self._tasks.pop(session_id, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _loop(self, session: EvacuationSession) -> None:
        try:
            while True:
                await asyncio.sleep(POLL_SECONDS)
                try:
                    await run_once(session)
                except Exception as exc:
                    # A failed poll must not kill the monitor. The next pass may
                    # be the one that matters.
                    session.step(
                        StepKind.MONITOR,
                        "Monitor pass failed",
                        detail=str(exc)[:200],
                        outcome="error",
                    )
        except asyncio.CancelledError:
            session.monitoring = False
            raise


supervisor = MonitorSupervisor()
