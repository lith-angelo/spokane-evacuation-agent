"""Mapbox temporary geocoding and candidate route generation.

Mapbox is optional and used only when explicitly configured.  In the replay
demo it can pass through the fixture layer so the resident's location and route
are real while fire, evacuation and closure feeds remain deterministic.
"""

from __future__ import annotations

from app.config import settings
from app.egress import EgressResult, Outcome, egress
from app.geo import haversine_km
from app.models import Place, Record, RouteCandidate, SourceId

_GEOCODE = "https://api.mapbox.com/search/geocode/v6/forward"
_DIRECTIONS = "https://api.mapbox.com/directions/v5/mapbox/driving-traffic"

GEOCODE_TTL = 7 * 24 * 3600
ROUTE_TTL = 900

# This product currently has authoritative evacuation and shelter coverage for
# Spokane County only.  A proximity hint is not a boundary: without bbox,
# Mapbox can resolve a shared street name thousands of kilometres away.
_SPOKANE_BBOX = (-118.20, 47.28, -116.85, 48.10)  # west, south, east, north
_MAX_ROUTE_DISTANCE_KM = 250.0


def _missing_token(url: str) -> EgressResult:
    return EgressResult(
        outcome=Outcome.UPSTREAM_ERROR,
        url=url,
        host="api.mapbox.com",
        error="MAPBOX_ACCESS_TOKEN is not configured",
    )


async def search(query: str, *, limit: int = 5) -> tuple[list[Place], EgressResult]:
    if not settings.mapbox_access_token:
        return [], _missing_token(_GEOCODE)

    result = await egress.fetch(
        _GEOCODE,
        params={
            "q": query,
            "limit": max(1, min(limit, 5)),
            "country": "US",
            "bbox": ",".join(str(value) for value in _SPOKANE_BBOX),
            "proximity": "-117.4260,47.6588",
            "access_token": settings.mapbox_access_token,
        },
        bypass_replay=settings.live_location_in_replay,
    )
    if not result.ok:
        return [], result

    data = result.json()
    features = data.get("features") if isinstance(data, dict) else None
    if not features:
        return [], result

    west, south, east, north = _SPOKANE_BBOX
    places: list[Place] = []
    for feature in features:
        try:
            lon, lat = (float(v) for v in feature["geometry"]["coordinates"][:2])
        except (KeyError, TypeError, ValueError):
            continue
        if not (west <= lon <= east and south <= lat <= north):
            continue

        properties = feature.get("properties") or {}
        label = (
            properties.get("full_address")
            or properties.get("name_preferred")
            or properties.get("name")
            or query
        )
        places.append(
            Place(
                lat=lat,
                lon=lon,
                label=label,
                record=Record(
                    record_id=f"mapbox:{feature.get('id', 'unknown')}",
                    source_id=SourceId.MAPBOX,
                    data_class="official",
                    ttl_seconds=GEOCODE_TTL,
                    provenance_url=_GEOCODE,
                    geometry={"type": "Point", "coordinates": [lon, lat]},
                    payload=feature,
                ),
            )
        )

    if not places:
        result.outcome = Outcome.UPSTREAM_ERROR
        result.error = "location is outside the Spokane County service area"
    return places, result


async def geocode(query: str) -> tuple[Place | None, EgressResult]:
    places, result = await search(query, limit=1)
    if not places and result.ok:
        result.outcome = Outcome.UPSTREAM_ERROR
        result.error = "Mapbox returned no matching location"
    return (places[0] if places else None), result


async def plan_routes(
    origin: tuple[float, float],
    destination: tuple[float, float],
    *,
    alternatives: int = 3,
) -> tuple[list[RouteCandidate], EgressResult]:
    if not settings.mapbox_access_token:
        return [], _missing_token(_DIRECTIONS)

    origin_lat, origin_lon = origin
    dest_lat, dest_lon = destination
    straight_line_km = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
    if straight_line_km > _MAX_ROUTE_DISTANCE_KM:
        result = _missing_token(_DIRECTIONS)
        result.error = (
            f"route endpoints are {straight_line_km:.0f} km apart, outside the "
            "Spokane evacuation service area"
        )
        return [], result
    url = f"{_DIRECTIONS}/{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
    result = await egress.fetch(
        url,
        params={
            "alternatives": "true" if alternatives > 1 else "false",
            "geometries": "geojson",
            "overview": "full",
            "steps": "false",
            "access_token": settings.mapbox_access_token,
        },
        bypass_replay=settings.live_location_in_replay,
    )
    if not result.ok:
        return [], result

    data = result.json()
    if not isinstance(data, dict) or data.get("code") != "Ok":
        result.outcome = Outcome.UPSTREAM_ERROR
        result.error = (data or {}).get("message", "Mapbox returned no route")
        return [], result

    out: list[RouteCandidate] = []
    for index, route in enumerate((data.get("routes") or [])[:alternatives]):
        geometry = route.get("geometry")
        if not geometry or not geometry.get("coordinates"):
            continue
        label = chr(ord("A") + index)
        distance_km = float(route.get("distance", 0)) / 1000
        if distance_km <= 0 or distance_km > _MAX_ROUTE_DISTANCE_KM:
            continue
        out.append(
            RouteCandidate(
                route_id=f"route-{label}",
                geometry=geometry,
                distance_km=round(distance_km, 2),
                eta_min=round(float(route.get("duration", 0)) / 60, 1),
                summary=f"Mapbox route {label}",
                record=Record(
                    record_id=f"mapbox:route:{label}",
                    source_id=SourceId.MAPBOX,
                    data_class="derived",
                    ttl_seconds=ROUTE_TTL,
                    provenance_url=url,
                    geometry=geometry,
                    payload={
                        "distance_m": route.get("distance"),
                        "duration_s": route.get("duration"),
                        "weight_name": route.get("weight_name"),
                    },
                ),
            )
        )
    return out, result
