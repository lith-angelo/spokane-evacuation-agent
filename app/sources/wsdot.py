"""WSDOT — state highway alerts and traffic-management-centre events.

Tier 1 for state routes. It knows nothing about county roads, which is why SREC's
own closure layer is queried alongside it.
"""

from __future__ import annotations

from typing import Any

from app.egress import EgressResult
from app.models import Closure, Record, SourceId, from_epoch_ms
from app.sources.arcgis import LayerQuery, query_layer

_ROOT = "https://data.wsdot.wa.gov/arcgis/rest/services"

ROAD_ALERTS = LayerQuery(
    base_url=f"{_ROOT}/TravelInformation/TravelInfoRoadAlerts/FeatureServer/0",
    out_fields="*",
)

# The EOC feed's only layer is id 1, not 0.
TMC_EVENTS = LayerQuery(
    base_url=f"{_ROOT}/EOC_TrafficEvents/TrafficMgmtCenterEvents/FeatureServer/1",
    out_fields="*",
)

CLOSURE_TTL = 1800

# Words that mean the road is shut, not merely slow. Anything matching becomes a
# hard closure and is a route-rejecting condition; everything else is a warning.
_HARD = ("closed", "closure", "blocked", "no travel", "road closed", "fully blocked")
_WILDFIRE = ("fire", "wildfire", "smoke", "burn", "evacuation")


async def get_closures(
    lat: float, lon: float, radius_km: float = 60.0
) -> tuple[list[Closure], EgressResult, EgressResult]:
    """State highway closures and alerts near a point.

    Returns both layers' egress results so coverage can be reported per feed.
    """
    alert_feats, alert_res = await query_layer(
        ROAD_ALERTS, lat=lat, lon=lon, radius_km=radius_km, result_record_count=200
    )
    tmc_feats, tmc_res = await query_layer(
        TMC_EVENTS, lat=lat, lon=lon, radius_km=radius_km, result_record_count=200
    )

    closures: list[Closure] = []
    for feat in alert_feats:
        c = _to_closure(feat, SourceId.WSDOT, ROAD_ALERTS.base_url)
        if c:
            closures.append(c)
    for feat in tmc_feats:
        c = _to_closure(feat, SourceId.WSDOT_EOC, TMC_EVENTS.base_url)
        if c:
            closures.append(c)

    return closures, alert_res, tmc_res


def _to_closure(feat: dict[str, Any], source: SourceId, base_url: str) -> Closure | None:
    a = feat.get("attributes") or {}
    oid = a.get("OBJECTID") or a.get("objectid") or a.get("AlertID") or a.get("EventID")

    description = _first(
        a,
        "HeadlineMessage",
        "HeadlineDescription",
        "Description",
        "EventDescription",
        "ExtendedDescription",
    )
    road = _first(a, "Road", "RoadwayName", "StateRouteID", "RouteID", "RoadName")
    if not description and not road:
        return None

    # WSDOT states this outright on the alerts layer, so believe the flag rather
    # than guessing from prose. Keyword matching is only the fallback for feeds
    # that carry no explicit flag.
    flag = a.get("RoadClosedFlag")
    if flag is not None:
        is_hard = str(flag).strip().lower() in ("1", "true", "yes", "y")
    else:
        blob = f"{description or ''} {a.get('EventCategoryDescription') or ''}".lower()
        is_hard = any(k in blob for k in _HARD)

    geom = feat.get("geometry") or {}
    clat, clon = geom.get("y"), geom.get("x")

    observed = from_epoch_ms(
        a.get("LastModifiedDate")
        or a.get("LastUpdatedTime")
        or a.get("StartTime")
        or a.get("EventStartTime")
    )

    return Closure(
        closure_id=f"{source.value.lower()}:{oid}",
        description=(description or f"{road} alert").strip(),
        road=road,
        lat=clat,
        lon=clon,
        geometry=feat.get("geometry"),
        severity="closure" if is_hard else "alert",
        is_hard_closure=is_hard,
        record=Record(
            record_id=f"{source.value.lower()}:closure:{oid}",
            source_id=source,
            data_class="official",
            observed_at=observed,
            ttl_seconds=CLOSURE_TTL,
            provenance_url=f"{base_url}/query",
            geometry=feat.get("geometry"),
            payload=a,
        ),
    )


def is_wildfire_related(closure: Closure) -> bool:
    return any(k in closure.description.lower() for k in _WILDFIRE)


def _first(a: dict[str, Any], *keys: str) -> str | None:
    for k in keys:
        v = a.get(k)
        if v not in (None, "", " "):
            return str(v).strip()
    return None
