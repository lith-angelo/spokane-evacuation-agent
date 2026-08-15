"""NIFC WFIGS — active incident locations and interagency perimeters.

Tier 1, federal, national coverage. This is the fire-geometry authority; it says
nothing about evacuation levels, which come from SREC.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.config import AGOL_ORG_NIFC
from app.egress import EgressResult, Outcome
from app.geo import haversine_km, point_distance_km, to_geometry
from app.models import Incident, Record, SourceId, from_epoch_ms
from app.sources.arcgis import LayerQuery, query_layer

_ROOT = f"https://services3.arcgis.com/{AGOL_ORG_NIFC}/arcgis/rest/services"

INCIDENTS = LayerQuery(
    base_url=f"{_ROOT}/WFIGS_Incident_Locations_Current/FeatureServer/0",
    out_fields=",".join(
        [
            "OBJECTID",
            "IncidentName",
            "IncidentSize",
            "PercentContained",
            "FireDiscoveryDateTime",
            "ModifiedOnDateTime_dt",
            "POOCounty",
            "POOState",
            "IncidentTypeCategory",
        ]
    ),
)

PERIMETERS = LayerQuery(
    base_url=f"{_ROOT}/WFIGS_Interagency_Perimeters_Current/FeatureServer/0",
    out_fields=",".join(
        [
            "OBJECTID",
            "poly_IncidentName",
            "poly_GISAcres",
            "poly_PolygonDateTime",
            "attr_IncidentName",
            "attr_PercentContained",
            "attr_ModifiedOnDateTime_dt",
        ]
    ),
)

# TTLs are operational, not architectural. A perimeter is remapped roughly once
# per operational period, so a 12-hour-old polygon is normal and current; a
# 30-minute TTL would mark every real perimeter stale and train the reader to
# ignore the flag. `stale` has to mean "too old to justify lowering the alarm"
# (DESIGN section 7.5) or it means nothing.
INCIDENT_TTL = 12 * 3600
PERIMETER_TTL = 24 * 3600


async def get_active_incidents(
    lat: float, lon: float, radius_km: float = 80.0
) -> tuple[list[Incident], EgressResult, EgressResult]:
    """Incidents within `radius_km`, each with its perimeter where one exists.

    Returns both egress results so the caller can report each layer's coverage
    separately — perimeters being blocked while points succeed is a materially
    different answer from both succeeding.
    """
    incident_feats, inc_res = await query_layer(
        INCIDENTS, lat=lat, lon=lon, radius_km=radius_km, result_record_count=100
    )
    perimeter_feats, per_res = await query_layer(
        PERIMETERS, lat=lat, lon=lon, radius_km=radius_km, result_record_count=100
    )

    perimeters = _index_perimeters(perimeter_feats)

    incidents: list[Incident] = []
    for feat in incident_feats:
        inc = _to_incident(feat, lat, lon, perimeters)
        if inc is not None:
            incidents.append(inc)

    incidents.sort(key=lambda i: (i.distance_km if i.distance_km is not None else 1e9))
    return incidents, inc_res, per_res


def _index_perimeters(features: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for f in features:
        a = f.get("attributes") or {}
        name = a.get("poly_IncidentName") or a.get("attr_IncidentName")
        geom = f.get("geometry")
        if not name or not geom:
            continue
        out[str(name).strip().upper()] = {
            "geometry": geom,
            "observed_at": from_epoch_ms(
                a.get("poly_PolygonDateTime") or a.get("attr_ModifiedOnDateTime_dt")
            ),
            "acres": a.get("poly_GISAcres"),
        }
    return out


def _to_incident(
    feat: dict[str, Any], lat: float, lon: float, perimeters: dict[str, dict[str, Any]]
) -> Incident | None:
    a = feat.get("attributes") or {}
    name = a.get("IncidentName")
    if not name:
        return None

    geom = feat.get("geometry") or {}
    ilat, ilon = geom.get("y"), geom.get("x")

    key = str(name).strip().upper()
    perim = perimeters.get(key)
    perim_geom = perim["geometry"] if perim else None

    # Prefer distance to the perimeter when we have one: the edge of the fire is
    # what matters to a resident, not the label point at its centre.
    distance = None
    if perim_geom is not None:
        distance = point_distance_km(lat, lon, perim_geom)
    if distance is None and ilat is not None and ilon is not None:
        distance = haversine_km(lat, lon, ilat, ilon)

    observed = from_epoch_ms(a.get("ModifiedOnDateTime_dt"))
    if perim and perim.get("observed_at"):
        observed = min(filter(None, [observed, perim["observed_at"]]), default=observed)

    oid = a.get("OBJECTID")
    record = Record(
        record_id=f"wfigs:{key}:{oid}",
        source_id=SourceId.WFIGS,
        data_class="official",
        observed_at=observed,
        ttl_seconds=PERIMETER_TTL if perim_geom is not None else INCIDENT_TTL,
        provenance_url=f"{INCIDENTS.base_url}/query",
        geometry=perim_geom,
        payload=a,
    )

    return Incident(
        incident_id=f"wfigs:{oid}",
        name=str(name).strip(),
        lat=ilat,
        lon=ilon,
        acres=_num(a.get("IncidentSize")),
        containment_pct=_num(a.get("PercentContained")),
        distance_km=distance,
        county=a.get("POOCounty"),
        category=a.get("IncidentTypeCategory"),
        discovered_at=from_epoch_ms(a.get("FireDiscoveryDateTime")),
        perimeter=perim_geom,
        record=record,
    )


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def hazard_geometries(incidents: list[Incident]) -> list[Any]:
    """Perimeter geometries usable for route validation.

    Only real perimeters. An incident point with no polygon is a location, not
    an extent, and buffering it into a fake circle would invent a hazard
    boundary the source never asserted.
    """
    out = []
    for inc in incidents:
        g = to_geometry(inc.perimeter)
        if g is not None:
            out.append(g)
    return out
