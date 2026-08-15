"""Persistent evacuation session state.

The book's core runtime state (section 2). This is what makes the thing an
always-on agent rather than a question-answering endpoint: household needs,
the selected shelter, the current route and the last validation all survive
across turns, and the monitor reads them without a user message.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app import store
from app.models import (
    BlockedAction,
    Closure,
    Consensus,
    EvacLevel,
    EvacZone,
    HouseholdNeeds,
    Incident,
    Place,
    RouteCandidate,
    Shelter,
    SourceStatus,
    Step,
    StepKind,
    Verdict,
    iso,
    utcnow,
)


@dataclass
class EvacuationSession:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    # Who and where
    query: str = ""
    place: Place | None = None
    needs: HouseholdNeeds = field(default_factory=HouseholdNeeds)

    # What we found
    zone: EvacZone | None = None
    zones: list[EvacZone] = field(default_factory=list)
    incidents: list[Incident] = field(default_factory=list)
    shelters: list[Shelter] = field(default_factory=list)
    rejected_shelters: list[tuple[Shelter, list[str]]] = field(default_factory=list)
    closures: list[Closure] = field(default_factory=list)

    # What we decided
    routes: list[RouteCandidate] = field(default_factory=list)
    approved_routes: list[RouteCandidate] = field(default_factory=list)
    rejected_routes: list[RouteCandidate] = field(default_factory=list)
    destination: Shelter | None = None
    current_route: RouteCandidate | None = None
    verdict: Verdict | None = None
    consensus: Consensus | None = None

    # Provenance and containment
    sources: list[SourceStatus] = field(default_factory=list)
    blocked: list[BlockedAction] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)

    # Re-entry
    hazmat_cleared: bool | None = None
    hazmat_note: str | None = None

    # Monitoring
    monitoring: bool = False
    last_checked_at: datetime | None = None
    monitor_events: list[dict[str, Any]] = field(default_factory=list)
    simulated_closure_active: bool = False

    # Notification authorisation (book section 7): only recipients the user has
    # explicitly provided in this session may ever be contacted.
    approved_contacts: list[str] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)

    _seq: int = 0
    _subscribers: list[asyncio.Queue] = field(default_factory=list, repr=False)

    # --- step trace ---------------------------------------------------------

    def step(
        self,
        kind: StepKind,
        label: str,
        *,
        detail: str | None = None,
        arguments: dict[str, Any] | None = None,
        outcome: str | None = None,
        latency_ms: int | None = None,
        simulated: bool = False,
    ) -> Step:
        self._seq += 1
        s = Step(
            seq=self._seq,
            kind=kind,
            label=label,
            detail=detail,
            arguments=arguments,
            outcome=outcome,
            latency_ms=latency_ms,
            simulated=simulated,
        )
        self.steps.append(s)
        self.updated_at = utcnow()
        try:
            store.append_step(self.session_id, s)
        except Exception:
            # The trace is an observability aid. Losing a row must never take
            # down an evacuation answer.
            pass
        self._publish({"type": "step", "step": s.model_dump(mode="json")})
        return s

    # --- SSE fan-out --------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def _publish(self, message: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass

    def publish(self, message: dict[str, Any]) -> None:
        self._publish(message)

    # --- persistence --------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Everything the UI needs, in one JSON-safe object."""
        return {
            "session_id": self.session_id,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
            "query": self.query,
            "place": self.place.model_dump(mode="json") if self.place else None,
            "needs": self.needs.model_dump(mode="json"),
            # The level the *guard* settled on, not the raw zone lookup. When a
            # fire is on top of an address that has no published zone, the guard
            # escalates and the header must show the escalated level — showing
            # "Unknown" beside a headline of "Level 2 — SET" would read as a bug
            # and, worse, as reassurance.
            "level": (
                self.verdict.level if self.verdict else
                (self.zone.level if self.zone else EvacLevel.UNKNOWN)
            ).value,
            "level_label": (
                self.verdict.level if self.verdict else
                (self.zone.level if self.zone else EvacLevel.UNKNOWN)
            ).label,
            "declared_level_label": (
                self.zone.level if self.zone else EvacLevel.UNKNOWN
            ).label,
            "zone": self.zone.model_dump(mode="json") if self.zone else None,
            "zones": [z.model_dump(mode="json") for z in self.zones],
            "incidents": [i.model_dump(mode="json") for i in self.incidents],
            "shelters": [s.model_dump(mode="json") for s in self.shelters],
            "rejected_shelters": [
                {"shelter": s.model_dump(mode="json"), "unmet": u}
                for s, u in self.rejected_shelters
            ],
            "closures": [c.model_dump(mode="json") for c in self.closures],
            "routes": [r.model_dump(mode="json") for r in self.routes],
            "approved_routes": [r.model_dump(mode="json") for r in self.approved_routes],
            "rejected_routes": [r.model_dump(mode="json") for r in self.rejected_routes],
            "destination": self.destination.model_dump(mode="json") if self.destination else None,
            "current_route": (
                self.current_route.model_dump(mode="json") if self.current_route else None
            ),
            "verdict": self.verdict.model_dump(mode="json") if self.verdict else None,
            "consensus": self.consensus.model_dump(mode="json") if self.consensus else None,
            "sources": [s.model_dump(mode="json") for s in self.sources],
            "blocked": [b.model_dump(mode="json") for b in self.blocked],
            "steps": [s.model_dump(mode="json") for s in self.steps],
            "monitoring": self.monitoring,
            "last_checked_at": iso(self.last_checked_at),
            "monitor_events": self.monitor_events,
            "simulated_closure_active": self.simulated_closure_active,
            "approved_contacts": self.approved_contacts,
            "notifications": self.notifications,
            "hazmat_cleared": self.hazmat_cleared,
            "hazmat_note": self.hazmat_note,
        }

    def persist(self) -> None:
        try:
            store.save_session(
                self.session_id,
                iso(self.created_at) or "",
                iso(self.updated_at) or "",
                self.snapshot(),
            )
        except Exception:
            pass

    def record_source(self, status: SourceStatus) -> None:
        """Replace any earlier status for the same source, keeping one row each."""
        self.sources = [s for s in self.sources if s.source_id is not status.source_id]
        self.sources.append(status)


class SessionRegistry:
    """In-memory registry. One process, one demo — deliberately not a cluster."""

    def __init__(self) -> None:
        self._sessions: dict[str, EvacuationSession] = {}
        self._lock = asyncio.Lock()

    def create(self) -> EvacuationSession:
        s = EvacuationSession()
        self._sessions[s.session_id] = s
        return s

    def get(self, session_id: str) -> EvacuationSession | None:
        return self._sessions.get(session_id)

    def all(self) -> list[EvacuationSession]:
        return list(self._sessions.values())

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock


registry = SessionRegistry()
