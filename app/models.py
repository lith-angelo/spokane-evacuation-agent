"""Records and the provenance envelope.

Every value that can influence a safety decision travels inside a `Record`, so
that "what is true", "who said so", and "when" cannot drift apart. Freshness is
computed here from `fetched_at + ttl_seconds` — never asserted by a source, and
never assumed by a caller.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def from_epoch_ms(ms: Any) -> datetime | None:
    """ArcGIS hands back epoch milliseconds, sometimes null, sometimes 0."""
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)
    except (ValueError, OSError, OverflowError, TypeError):
        return None


class SourceId(str, Enum):
    WFIGS = "WFIGS"
    SREC = "SREC"
    SPOKANE_GIS = "SPOKANE_GIS"
    WSDOT = "WSDOT"
    WSDOT_EOC = "WSDOT_EOC"
    NOMINATIM = "NOMINATIM"
    OSRM = "OSRM"
    MAPBOX = "MAPBOX"
    FIRECAM = "FIRECAM"
    DERIVED = "DERIVED"


DataClass = Literal["official", "derived", "replay", "synthetic"]

# Tier 1 is an incident-command or federal authority speaking about its own
# jurisdiction. Tier 2 is a real authority speaking about something adjacent.
# Tier 3 is infrastructure we depend on but which asserts nothing about hazard.
AUTHORITY_TIER: dict[SourceId, int] = {
    SourceId.SREC: 1,
    SourceId.WFIGS: 1,
    SourceId.WSDOT: 1,
    SourceId.WSDOT_EOC: 2,
    SourceId.SPOKANE_GIS: 2,
    SourceId.DERIVED: 2,
    SourceId.NOMINATIM: 3,
    SourceId.OSRM: 3,
    SourceId.MAPBOX: 3,
    SourceId.FIRECAM: 3,
}


class Record(BaseModel):
    """The provenance envelope (DESIGN section 5)."""

    record_id: str
    source_id: SourceId
    data_class: DataClass = "official"
    observed_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=utcnow)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    ttl_seconds: int = 900
    provenance_url: str | None = None
    geometry: dict[str, Any] | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def authority_tier(self) -> int:
        return AUTHORITY_TIER.get(self.source_id, 3)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def stale(self) -> bool:
        """Computed, never asserted.

        Age is measured from `observed_at` when the source tells us when it
        observed the thing, because a fresh fetch of an eight-hour-old
        observation is an eight-hour-old fact.
        """
        basis = self.observed_at or self.fetched_at
        return (utcnow() - basis).total_seconds() > self.ttl_seconds

    @computed_field  # type: ignore[prop-decorator]
    @property
    def age_seconds(self) -> int:
        basis = self.observed_at or self.fetched_at
        return max(0, int((utcnow() - basis).total_seconds()))

    @property
    def as_of(self) -> str:
        """The string every safety-bearing number is rendered with."""
        when = iso(self.observed_at or self.fetched_at)
        return f"{self.source_id.value}, as of {when}"


class EvacLevel(int, Enum):
    """Washington's three-level standard, plus an explicit unknown.

    Ordered so that comparison is meaningful: `max()` over levels is the
    conservative choice. UNKNOWN is deliberately *not* zero — absence of a
    published zone is not an All Clear (DESIGN section 2.2), so it must never
    win a `max()` against a real level, and must never be read as "clear".
    """

    UNKNOWN = -1
    NONE = 0
    LEVEL_1 = 1  # Ready
    LEVEL_2 = 2  # Set
    LEVEL_3 = 3  # Go

    @property
    def label(self) -> str:
        return {
            EvacLevel.UNKNOWN: "Unknown",
            EvacLevel.NONE: "No active zone",
            EvacLevel.LEVEL_1: "Level 1 — READY",
            EvacLevel.LEVEL_2: "Level 2 — SET",
            EvacLevel.LEVEL_3: "Level 3 — GO NOW",
        }[self]

    @property
    def is_actionable(self) -> bool:
        return self in (EvacLevel.LEVEL_2, EvacLevel.LEVEL_3)

    @staticmethod
    def parse(raw: Any) -> "EvacLevel":
        """Parse SREC's free-text `EvacLevel` / `EvacStatus`.

        Unrecognised text becomes UNKNOWN rather than NONE. A field we cannot
        read is not an absence of danger.
        """
        if raw is None:
            return EvacLevel.UNKNOWN
        s = str(raw).strip().lower()
        if not s:
            return EvacLevel.UNKNOWN
        if "3" in s or "go" in s:
            return EvacLevel.LEVEL_3
        if "2" in s or "set" in s:
            return EvacLevel.LEVEL_2
        if "1" in s or "ready" in s:
            return EvacLevel.LEVEL_1
        if "normal" in s or "no evac" in s or "none" in s or "clear" in s:
            return EvacLevel.NONE
        return EvacLevel.UNKNOWN


class HouseholdNeeds(BaseModel):
    """Persists for the life of the session until the user changes it."""

    pets: bool = False
    service_animal: bool = False
    mobility: bool = False  # wheelchair / walker / non-ambulatory
    medical: bool = False  # oxygen, dialysis, refrigerated meds
    language: str | None = None
    people: int = 1
    notes: str | None = None

    @property
    def hard_constraints(self) -> list[str]:
        """The needs that filter shelters. Never traded against distance."""
        out = []
        if self.pets:
            out.append("pets")
        if self.service_animal:
            out.append("service_animal")
        if self.mobility:
            out.append("mobility")
        if self.medical:
            out.append("medical")
        return out

    def describe(self) -> str:
        bits = []
        if self.people > 1:
            bits.append(f"{self.people} people")
        if self.pets:
            bits.append("pets")
        if self.service_animal:
            bits.append("service animal")
        if self.mobility:
            bits.append("mobility assistance")
        if self.medical:
            bits.append("medical needs")
        if self.language:
            bits.append(f"language: {self.language}")
        return ", ".join(bits) or "no stated constraints"


class Place(BaseModel):
    lat: float
    lon: float
    label: str | None = None
    record: Record | None = None


class EvacZone(BaseModel):
    zone_id: str
    name: str | None = None
    level: EvacLevel = EvacLevel.UNKNOWN
    raw_level: str | None = None
    status: str | None = None
    incident_name: str | None = None
    boundary_desc: str | None = None
    public_message: str | None = None
    record: Record


class Incident(BaseModel):
    incident_id: str
    name: str
    lat: float | None = None
    lon: float | None = None
    acres: float | None = None
    containment_pct: float | None = None
    distance_km: float | None = None
    county: str | None = None
    category: str | None = None
    discovered_at: datetime | None = None
    perimeter: dict[str, Any] | None = None
    record: Record

    def render_size(self) -> str:
        """Revisable numbers are always rendered value + source + as-of."""
        if self.acres is None:
            return f"size not reported ({self.record.as_of})"
        return f"{self.acres:,.0f} acres ({self.record.as_of})"


class Shelter(BaseModel):
    shelter_id: str
    name: str
    address: str | None = None
    lat: float
    lon: float
    distance_km: float | None = None
    accepts: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    capacity_status: str | None = None
    capacity_known: bool = False
    facility_type: str | None = None
    record: Record

    def unmet(self, needs: HouseholdNeeds) -> list[str]:
        """Hard constraints this shelter does not satisfy.

        Silence is not consent: a capability the source does not affirm is
        treated as absent (DESIGN section 2.2), so an unknown-capability shelter
        fails the filter rather than passing it.
        """
        have = {c.lower() for c in self.capabilities} | {a.lower() for a in self.accepts}
        missing = []
        for need in needs.hard_constraints:
            if need not in have:
                missing.append(need)
        return missing


class Closure(BaseModel):
    closure_id: str
    description: str
    road: str | None = None
    lat: float | None = None
    lon: float | None = None
    geometry: dict[str, Any] | None = None
    severity: str | None = None
    is_hard_closure: bool = False
    simulated: bool = False
    record: Record


class RouteCandidate(BaseModel):
    route_id: str
    geometry: dict[str, Any]  # GeoJSON LineString
    distance_km: float
    eta_min: float
    summary: str | None = None
    record: Record

    # Filled in by the validator. Never by the router, and never by the model.
    approved: bool | None = None
    rejection_reason: str | None = None
    hazard_margin_km: float | None = None
    intersects: list[str] = Field(default_factory=list)


class BlockedAction(BaseModel):
    """A capability the agent genuinely attempted and the policy refused."""

    tool: str
    host: str
    method: str = "GET"
    path: str = ""
    policy: str | None = None
    rule: str | None = None
    detail: str | None = None
    layer: str = "l7"
    at: datetime = Field(default_factory=utcnow)


class SourceStatus(BaseModel):
    """Per-source coverage, so the answer can say what it does not know."""

    source_id: SourceId
    outcome: str  # OK | POLICY_DENIED | UPSTREAM_ERROR | SANDBOX_UNAVAILABLE | REPLAY
    detail: str | None = None
    record_count: int = 0
    stale_count: int = 0
    fetched_at: datetime = Field(default_factory=utcnow)

    @property
    def usable(self) -> bool:
        return self.outcome in ("OK", "REPLAY")


class Consensus(BaseModel):
    """Cross-source agreement on evacuation status.

    `agreed=False` is never resolved silently — it is surfaced, and the
    conservative value is the one that is acted on.
    """

    agreed: bool
    confidence: Literal["high", "medium", "low"]
    level: EvacLevel
    sources_checked: list[SourceId] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    explanation: str = ""


class StepKind(str, Enum):
    MODEL = "model"
    TOOL = "tool"
    GUARD = "safety guard"
    MONITOR = "monitor"
    BLOCKED = "blocked"


class Step(BaseModel):
    """One line of the judge-facing activity panel.

    Observable execution only: tool names, arguments, outcomes, timings,
    decisions. Never model deliberation (book section 8).
    """

    seq: int
    kind: StepKind
    label: str
    detail: str | None = None
    arguments: dict[str, Any] | None = None
    outcome: str | None = None
    latency_ms: int | None = None
    simulated: bool = False
    at: datetime = Field(default_factory=utcnow)


class Verdict(BaseModel):
    """Owned by app/safety.py. The model may phrase it; it may not set it."""

    recommended_action: str
    headline: str
    level: EvacLevel
    urgent: bool = False
    destination: str | None = None
    route_summary: str | None = None
    critical_warnings: list[str] = Field(default_factory=list)
    unverified: list[str] = Field(default_factory=list)
    freshness_summary: str = ""
    next_monitor_condition: str | None = None
    can_return_home: bool = False

    # The model's prose, rendered *around* the fields above and never in place
    # of them. If this is empty the answer is still complete; if it contradicts
    # the fields above, the fields above are what the UI acts on.
    narrative: str | None = None
