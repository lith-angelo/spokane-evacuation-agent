"""NASA FIRMS near-real-time satellite thermal detections.

FIRMS points are an independent live-detection layer. They are never promoted
to an official incident, evacuation zone, or mapped fire perimeter: a thermal
anomaly can have non-wildfire causes and a point does not describe fire extent.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.egress import EgressResult, Outcome, egress
from app.geo import bbox_around, haversine_km, point_distance_km
from app.models import FireHotspot, Incident, Record, SourceId

HOST = "firms.modaps.eosdis.nasa.gov"
ROOT = f"https://{HOST}"
DEFAULT_SOURCE = "VIIRS_NOAA20_NRT"
MAX_RADIUS_KM = 500.0


def _error(message: str, *, url: str = ROOT) -> EgressResult:
    return EgressResult(
        outcome=Outcome.UPSTREAM_ERROR,
        url=url,
        host=HOST,
        error=message,
    )


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _acquired_at(row: dict[str, str]) -> datetime | None:
    date = (row.get("acq_date") or "").strip()
    raw_time = (row.get("acq_time") or "").strip().zfill(4)
    if not date or len(raw_time) != 4 or not raw_time.isdigit():
        return None
    try:
        return datetime.strptime(
            f"{date} {raw_time}", "%Y-%m-%d %H%M"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _confidence(value: str | None) -> str | None:
    raw = (value or "").strip()
    return {"l": "low", "n": "nominal", "h": "high"}.get(
        raw.lower(), raw or None
    )


def _safe_provenance(source: str, bbox: str, day_range: int) -> str:
    return f"{ROOT}/api/area/csv/[REDACTED]/{source}/{bbox}/{day_range}"


async def get_hotspots(
    lat: float,
    lon: float,
    *,
    radius_km: float = 80.0,
    day_range: int = 1,
    source: str = DEFAULT_SOURCE,
) -> tuple[list[FireHotspot], EgressResult]:
    """Fetch and normalize FIRMS detections within a true radial distance.

    FIRMS accepts a bounding box, so results are filtered again by haversine
    distance. The caller can therefore truthfully describe the requested
    radius rather than accidentally including the box's farther corners.
    """
    if not settings.firms_map_key and not getattr(settings, "replay", False):
        return [], _error("FIRMS_MAP_KEY is not configured")

    radius = min(MAX_RADIUS_KM, max(0.1, float(radius_km)))
    days = min(5, max(1, int(day_range)))
    bounds = bbox_around(lat, lon, radius)
    bbox = ",".join(f"{value:.5f}" for value in bounds)
    url = (
        f"{ROOT}/api/area/csv/{settings.firms_map_key or 'replay'}/"
        f"{source}/{bbox}/{days}"
    )
    result = await egress.fetch(url)
    if not result.ok:
        return [], result

    body = result.body.strip()
    if not body:
        return [], result
    if body.lower().startswith(("invalid map_key", "transaction limit")):
        return [], _error("FIRMS rejected the configured MAP_KEY", url=result.url)

    reader = csv.DictReader(io.StringIO(body))
    required = {"latitude", "longitude", "acq_date", "acq_time"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        return [], _error("FIRMS response was not a recognized CSV feed", url=result.url)

    provenance = _safe_provenance(source, bbox, days)
    hotspots: list[FireHotspot] = []
    for row in reader:
        hotspot_lat = _number(row.get("latitude"))
        hotspot_lon = _number(row.get("longitude"))
        acquired = _acquired_at(row)
        if hotspot_lat is None or hotspot_lon is None or acquired is None:
            continue
        distance = haversine_km(lat, lon, hotspot_lat, hotspot_lon)
        if distance > radius:
            continue

        satellite = (row.get("satellite") or "").strip() or None
        instrument = (row.get("instrument") or "").strip() or None
        hotspot_id = (
            f"firms:{satellite or source}:{acquired.strftime('%Y%m%d%H%M')}:"
            f"{hotspot_lat:.4f}:{hotspot_lon:.4f}"
        )
        record = Record(
            record_id=hotspot_id,
            source_id=SourceId.FIRMS,
            data_class="replay" if result.outcome is Outcome.REPLAY else "official",
            observed_at=acquired,
            ttl_seconds=settings.firms_hotspot_ttl_seconds,
            provenance_url=provenance,
            geometry={"type": "Point", "coordinates": [hotspot_lon, hotspot_lat]},
            payload={
                "source_product": source,
                "daynight": row.get("daynight"),
                "version": row.get("version"),
            },
        )
        hotspots.append(
            FireHotspot(
                hotspot_id=hotspot_id,
                lat=hotspot_lat,
                lon=hotspot_lon,
                acquired_at=acquired,
                satellite=satellite,
                instrument=instrument,
                confidence=_confidence(row.get("confidence")),
                fire_radiative_power_mw=_number(row.get("frp")),
                brightness_k=_number(
                    row.get("bright_ti4") or row.get("brightness")
                ),
                distance_km=distance,
                record=record,
            )
        )

    hotspots.sort(
        key=lambda hotspot: (
            hotspot.distance_km if hotspot.distance_km is not None else 1e9,
            -hotspot.acquired_at.timestamp(),
        )
    )
    return hotspots, result


def nearest_incident(
    hotspot: FireHotspot,
    incidents: list[Incident],
    *,
    max_distance_km: float = 10.0,
) -> tuple[Incident | None, float | None]:
    """Associate a detection for display without turning it into an incident."""
    best: tuple[Incident, float] | None = None
    for incident in incidents:
        distance = point_distance_km(hotspot.lat, hotspot.lon, incident.perimeter)
        if distance is None and incident.lat is not None and incident.lon is not None:
            distance = haversine_km(
                hotspot.lat, hotspot.lon, incident.lat, incident.lon
            )
        if distance is None or distance > max_distance_km:
            continue
        if best is None or distance < best[1]:
            best = (incident, distance)
    return best if best is not None else (None, None)
