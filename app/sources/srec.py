"""SREC — Spokane Regional Emergency Communications.

Tier 1 local authority. This is the only source in the build that states an
official evacuation level, and the only one that names shelters and emergency
facilities for the county.

Chosen over Genasys Protect, which serves an SPA rather than JSON at every
`/api/**` route we probed. See docs/SOURCES.md.
"""

from __future__ import annotations

from typing import Any

from app.config import AGOL_ORG_SREC
from app.egress import EgressResult
from app.geo import haversine_km, point_in
from app.models import Closure, EvacLevel, EvacZone, Record, Shelter, SourceId
from app.sources.arcgis import LayerQuery, query_layer

_ROOT = f"https://services3.arcgis.com/{AGOL_ORG_SREC}/arcgis/rest/services"

EVAC_AREAS = LayerQuery(
    base_url=f"{_ROOT}/Evacuation_Areas_Spokane_County_Public_View/FeatureServer/0",
    out_fields=",".join(
        [
            "OBJECTID",
            "IncidentType",
            "IncidentName",
            "FireDistrict",
            "EvacStatus",
            "EvacLevel",
            "BoundaryDesc",
            "PublicAppMsg",
        ]
    ),
)

FACILITIES = LayerQuery(
    base_url=f"{_ROOT}/Emergency_Response_Facility_Spokane_Co_Public_View/FeatureServer/0",
    out_fields="*",
)

# Support data published only while an evacuation is running. Layer 2 is the
# point-of-interest layer that carries activated shelters; layer 3 is SREC's own
# road-closure line layer, which is local incident-command detail that WSDOT's
# state-highway feed does not carry.
_SUPPORT = f"{_ROOT}/Evacuation_Support_Data_Spokane_County_Public_View/FeatureServer"

EVAC_POI = LayerQuery(
    base_url=f"{_SUPPORT}/2",
    out_fields="OBJECTID,POI_Type,POI_Name,Notes,FullAddress,PhoneNumber",
)

EVAC_CLOSURES = LayerQuery(
    base_url=f"{_SUPPORT}/3",
    out_fields="OBJECTID,RoadName,ActiveClosure,Notes",
)

# Evacuation levels change within an incident, not within a season.
EVAC_TTL = 3600
FACILITY_TTL = 7 * 24 * 3600


async def get_evacuation_zones(
    lat: float, lon: float, radius_km: float = 40.0
) -> tuple[list[EvacZone], EgressResult]:
    """Published evacuation areas near a point.

    An empty list means SREC published no zone here. That is *not* an all-clear
    and callers must not render it as one — see `zone_containing`.
    """
    feats, result = await query_layer(
        EVAC_AREAS, lat=lat, lon=lon, radius_km=radius_km, result_record_count=200
    )

    zones: list[EvacZone] = []
    for f in feats:
        a = f.get("attributes") or {}
        geom = f.get("geometry")
        oid = a.get("OBJECTID")

        raw = a.get("EvacLevel") or a.get("EvacStatus")
        zones.append(
            EvacZone(
                zone_id=f"srec:{oid}",
                name=a.get("BoundaryDesc") or a.get("IncidentName"),
                level=EvacLevel.parse(raw),
                raw_level=str(raw) if raw is not None else None,
                status=a.get("EvacStatus"),
                incident_name=a.get("IncidentName"),
                boundary_desc=a.get("BoundaryDesc"),
                public_message=a.get("PublicAppMsg"),
                record=Record(
                    record_id=f"srec:evac:{oid}",
                    source_id=SourceId.SREC,
                    data_class="official",
                    ttl_seconds=EVAC_TTL,
                    provenance_url=f"{EVAC_AREAS.base_url}/query",
                    geometry=geom,
                    payload=a,
                ),
            )
        )

    return zones, result


def zone_containing(lat: float, lon: float, zones: list[EvacZone]) -> EvacZone | None:
    """The published zone a point falls inside, if any.

    When a point is inside several overlapping zones the highest level wins:
    overlapping publications are resolved conservatively, never averaged and
    never resolved by recency.
    """
    hits = [z for z in zones if point_in(lat, lon, z.record.geometry)]
    if not hits:
        return None
    return max(hits, key=lambda z: (z.level.value, z.record.observed_at or z.record.fetched_at))


# Capability keywords as they appear in SREC facility attributes. A shelter only
# earns a capability when the source affirms it; see Shelter.unmet.
_CAPABILITY_HINTS: dict[str, tuple[str, ...]] = {
    "pets": ("pet", "kennel", "crate", "livestock", "animal intake"),
    "service_animal": ("service animal", "service dog"),
    "mobility": ("ada", "accessible", "wheelchair", "mobility"),
    "medical": ("medical", "nurse", "clinic", "health"),
}

# Shelter notes are prose written by dispatchers, and prose says "no pets" and
# "NOT ADA accessible" far more often than it says nothing at all. Substring
# matching reads both of those as capabilities, which is how a wheelchair user
# gets routed to a building with stairs at every entrance. Every hint match is
# therefore checked against the words immediately before it.
_NEGATIONS = (
    "no ",
    "not ",
    "non-",
    "without",
    "cannot",
    "can not",
    "unable",
    "prohibited",
    "unavailable",
    "lacks",
    "lack of",
)

_NEGATION_WINDOW = 26


def _affirms(blob: str, hint: str) -> bool:
    """Does `blob` affirm `hint`, rather than deny it?

    True only when at least one occurrence of the hint is not preceded by a
    negation. One clean mention is enough; a capability mentioned only inside a
    denial is not a capability.
    """
    start = 0
    while True:
        idx = blob.find(hint, start)
        if idx == -1:
            return False
        window = blob[max(0, idx - _NEGATION_WINDOW) : idx]
        if not any(neg in window for neg in _NEGATIONS):
            return True
        start = idx + len(hint)


def detect_capabilities(blob: str) -> list[str]:
    """Capabilities the source affirmatively states this site has."""
    blob = blob.lower()
    caps = [
        cap
        for cap, hints in _CAPABILITY_HINTS.items()
        if any(_affirms(blob, h) for h in hints)
    ]

    # "Service animals only" is an affirmation of one capability and a denial of
    # the other. Without this the phrase reads as a pet policy.
    if "service animal" in blob and "only" in blob:
        caps = [c for c in caps if c != "pets"]
        if "service_animal" not in caps:
            caps.append("service_animal")

    return sorted(caps)


async def get_facilities(
    lat: float, lon: float, radius_km: float = 40.0
) -> tuple[list[Shelter], EgressResult]:
    """Emergency response facilities as candidate shelters.

    SREC's facility layer describes buildings, not activated shelters, and it
    does not publish live occupancy. Every shelter therefore comes back with
    `capacity_known=False`, which the safety layer renders as an explicit
    unknown rather than as available space.
    """
    feats, result = await query_layer(
        FACILITIES, lat=lat, lon=lon, radius_km=radius_km, result_record_count=200
    )

    shelters: list[Shelter] = []
    for f in feats:
        a = f.get("attributes") or {}
        geom = f.get("geometry") or {}
        slat, slon = geom.get("y"), geom.get("x")
        if slat is None or slon is None:
            continue

        name = _first(a, "PlaceName", "POI_Name", "Name", "FacilityName", "SiteName")
        if not name:
            continue

        oid = a.get("OBJECTID") or a.get("objectid")
        capabilities = detect_capabilities(
            " ".join(str(v) for v in a.values() if v is not None)
        )

        street = _first(a, "Address", "FullAddress", "SiteAddress")
        city = _first(a, "City")
        address = ", ".join(x for x in (street, city) if x)

        shelters.append(
            Shelter(
                shelter_id=f"srec:fac:{oid}",
                name=str(name).strip(),
                address=address or None,
                lat=slat,
                lon=slon,
                distance_km=haversine_km(lat, lon, slat, slon),
                capabilities=capabilities,
                capacity_status=None,
                capacity_known=False,
                facility_type=_first(a, "PlaceType", "POI_Type", "Type", "FacilityType"),
                record=Record(
                    record_id=f"srec:fac:{oid}",
                    source_id=SourceId.SREC,
                    data_class="official",
                    ttl_seconds=FACILITY_TTL,
                    provenance_url=f"{FACILITIES.base_url}/query",
                    geometry={"type": "Point", "coordinates": [slon, slat]},
                    payload=a,
                ),
            )
        )

    shelters.sort(key=lambda s: s.distance_km or 1e9)
    return shelters, result


async def get_evacuation_poi(
    lat: float, lon: float, radius_km: float = 60.0
) -> tuple[list[Shelter], EgressResult]:
    """Activated evacuation points of interest — shelters, staging, comfort stations.

    Published only while an evacuation is running, so an empty result on a quiet
    day is correct and means "none activated", not "none exist".
    """
    feats, result = await query_layer(
        EVAC_POI, lat=lat, lon=lon, radius_km=radius_km, result_record_count=200
    )

    out: list[Shelter] = []
    for f in feats:
        a = f.get("attributes") or {}
        geom = f.get("geometry") or {}
        slat, slon = geom.get("y"), geom.get("x")
        name = _first(a, "POI_Name")
        if slat is None or slon is None or not name:
            continue

        oid = a.get("OBJECTID")
        capabilities = detect_capabilities(
            " ".join(str(v) for v in a.values() if v is not None)
        )

        out.append(
            Shelter(
                shelter_id=f"srec:poi:{oid}",
                name=str(name).strip(),
                address=_first(a, "FullAddress"),
                lat=slat,
                lon=slon,
                distance_km=haversine_km(lat, lon, slat, slon),
                capabilities=capabilities,
                capacity_status=None,
                capacity_known=False,
                facility_type=_first(a, "POI_Type"),
                record=Record(
                    record_id=f"srec:poi:{oid}",
                    source_id=SourceId.SREC,
                    data_class="official",
                    ttl_seconds=EVAC_TTL,
                    provenance_url=f"{EVAC_POI.base_url}/query",
                    geometry={"type": "Point", "coordinates": [slon, slat]},
                    payload=a,
                ),
            )
        )

    out.sort(key=lambda s: s.distance_km or 1e9)
    return out, result


async def get_local_closures(
    lat: float, lon: float, radius_km: float = 60.0
) -> tuple[list[Closure], EgressResult]:
    """SREC's own evacuation road closures.

    Local incident-command detail that the state highway feed does not carry:
    WSDOT knows about SR-291, not about a county road an engine company shut
    twenty minutes ago.
    """
    feats, result = await query_layer(
        EVAC_CLOSURES, lat=lat, lon=lon, radius_km=radius_km, result_record_count=200
    )

    out: list[Closure] = []
    for f in feats:
        a = f.get("attributes") or {}
        oid = a.get("OBJECTID")
        road = _first(a, "RoadName")
        notes = _first(a, "Notes")
        active = a.get("ActiveClosure")

        # Anything not explicitly inactive is treated as a live closure.
        is_active = str(active).strip().lower() not in ("no", "false", "0", "inactive")

        # Marked at the source so the label cannot be lost between here and the
        # screen. The book requires replayed and simulated events to be labelled
        # wherever they appear, and a flag set by one caller is a flag another
        # caller forgets.
        simulated = "SIMULATED" in (notes or "").upper()

        out.append(
            Closure(
                closure_id=f"srec:closure:{oid}",
                description=notes or f"{road or 'Road'} closed",
                road=road,
                geometry=f.get("geometry"),
                severity="closure" if is_active else "cleared",
                is_hard_closure=is_active,
                simulated=simulated,
                record=Record(
                    record_id=f"srec:closure:{oid}",
                    source_id=SourceId.SREC,
                    data_class="official",
                    ttl_seconds=1800,
                    provenance_url=f"{EVAC_CLOSURES.base_url}/query",
                    geometry=f.get("geometry"),
                    payload=a,
                ),
            )
        )

    return out, result


def _first(a: dict[str, Any], *keys: str) -> str | None:
    for k in keys:
        v = a.get(k)
        if v not in (None, "", " "):
            return str(v).strip()
    return None
