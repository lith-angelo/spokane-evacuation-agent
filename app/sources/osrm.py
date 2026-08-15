"""OSRM — candidate route generation.

Tier 3. This module generates candidates and nothing else. It never marks a
route safe, approved or recommended; that is `app/safety.py`'s decision and the
book (skill 5) is explicit that the router must not pre-empt it.

OSRM's graph knows nothing about today's closures, so closures are applied as a
post-filter in the validator. A route the validator rejects is an honest
outcome, not a routing bug.
"""

from __future__ import annotations

from urllib.parse import quote

from app.egress import EgressResult, Outcome, egress
from app.geo import encode_polyline
from app.models import Record, RouteCandidate, SourceId

_ROOT = "https://router.project-osrm.org"
ROUTE_TTL = 900


async def plan_routes(
    origin: tuple[float, float],
    destination: tuple[float, float],
    *,
    alternatives: int = 3,
) -> tuple[list[RouteCandidate], EgressResult]:
    """Candidate routes from origin to destination, both (lat, lon).

    Uses OSRM's `polyline(...)` input rather than `lon,lat;lon,lat` because the
    sandbox's L7 proxy truncates a path at `;` and OSRM would see a single
    coordinate. Verified in docs/SOURCES.md.
    """
    # Percent-encode the payload: polyline5 emits printable ASCII that includes
    # `|`, `\` and `` ` ``, which curl rejects in a raw URL.
    encoded = quote(encode_polyline([origin, destination]), safe="")
    url = f"{_ROOT}/route/v1/driving/polyline({encoded})"

    result = await egress.fetch(
        url,
        params={
            "overview": "full",
            "geometries": "geojson",
            "alternatives": str(alternatives),
            "steps": "false",
        },
    )
    if not result.ok:
        return [], result

    data = result.json()
    if not isinstance(data, dict) or data.get("code") != "Ok":
        result.outcome = Outcome.UPSTREAM_ERROR
        result.error = (data or {}).get("message", "router returned no route")
        return [], result

    routes = data.get("routes") or []
    out: list[RouteCandidate] = []
    for i, r in enumerate(routes):
        geom = r.get("geometry")
        if not geom or not geom.get("coordinates"):
            continue

        distance_km = float(r.get("distance", 0.0)) / 1000.0
        eta_min = float(r.get("duration", 0.0)) / 60.0
        label = chr(ord("A") + i)

        out.append(
            RouteCandidate(
                route_id=f"route-{label}",
                geometry=geom,
                distance_km=round(distance_km, 2),
                eta_min=round(eta_min, 1),
                summary=_summarize(r) or f"Route {label}",
                record=Record(
                    record_id=f"osrm:route:{label}",
                    source_id=SourceId.OSRM,
                    data_class="derived",
                    ttl_seconds=ROUTE_TTL,
                    provenance_url=url,
                    geometry=geom,
                    payload={
                        "distance_m": r.get("distance"),
                        "duration_s": r.get("duration"),
                        "weight_name": r.get("weight_name"),
                    },
                ),
            )
        )

    return out, result


def _summarize(route: dict) -> str | None:
    legs = route.get("legs") or []
    parts = [leg.get("summary") for leg in legs if leg.get("summary")]
    return ", ".join(parts) if parts else None
