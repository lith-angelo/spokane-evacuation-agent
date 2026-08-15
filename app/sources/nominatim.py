"""Nominatim geocoding — landmark or address to coordinates.

Tier 3: it locates the resident, it asserts nothing about hazard. Rate-limited
to 1 req/s by a dedicated lane inside `app/egress.py`.

The acceptance checklist requires a landmark to work with no hardcoded
coordinates, so this is the entry point for every request.
"""

from __future__ import annotations

from app.egress import EgressResult, Outcome, egress
from app.models import Place, Record, SourceId

_SEARCH = "https://nominatim.openstreetmap.org/search"
_REVERSE = "https://nominatim.openstreetmap.org/reverse"

# Spokane County, generously bounded. Keeps "Rifle Club Road" from resolving to
# a same-named road three states away.
_VIEWBOX = "-118.20,47.28,-116.85,48.10"

GEOCODE_TTL = 7 * 24 * 3600


async def geocode(query: str, *, bounded: bool = True) -> tuple[Place | None, EgressResult]:
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": 5,
        "addressdetails": 1,
        "countrycodes": "us",
    }
    if bounded:
        params["viewbox"] = _VIEWBOX
        params["bounded"] = 1

    result = await egress.fetch(_SEARCH, params=params)
    if not result.ok:
        return None, result

    data = result.json()
    if not isinstance(data, list) or not data:
        # Retry unbounded once: a resident may name a destination outside the
        # county. Still one Nominatim call at a time, per its usage policy.
        if bounded:
            return await geocode(query, bounded=False)
        return None, result

    best = data[0]
    try:
        lat, lon = float(best["lat"]), float(best["lon"])
    except (KeyError, TypeError, ValueError):
        result.outcome = Outcome.UPSTREAM_ERROR
        result.error = "geocoder returned no usable coordinates"
        return None, result

    place = Place(
        lat=lat,
        lon=lon,
        label=best.get("display_name") or query,
        record=Record(
            record_id=f"nominatim:{best.get('osm_type','?')}:{best.get('osm_id','?')}",
            source_id=SourceId.NOMINATIM,
            data_class="official",
            ttl_seconds=GEOCODE_TTL,
            provenance_url=_SEARCH,
            geometry={"type": "Point", "coordinates": [lon, lat]},
            payload=best,
        ),
    )
    return place, result


async def reverse(lat: float, lon: float) -> tuple[Place | None, EgressResult]:
    result = await egress.fetch(
        _REVERSE, params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 16}
    )
    if not result.ok:
        return None, result

    data = result.json()
    if not isinstance(data, dict) or "display_name" not in data:
        return None, result

    return (
        Place(
            lat=lat,
            lon=lon,
            label=data["display_name"],
            record=Record(
                record_id=f"nominatim:reverse:{lat:.5f},{lon:.5f}",
                source_id=SourceId.NOMINATIM,
                data_class="official",
                ttl_seconds=GEOCODE_TTL,
                provenance_url=_REVERSE,
                payload=data,
            ),
        ),
        result,
    )
