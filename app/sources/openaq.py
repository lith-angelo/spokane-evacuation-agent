"""OpenAQ v3 PM2.5 observations.

This is an internal evidence adapter, not a model-facing tool. Route validation
and shelter ranking ask for AQ evidence automatically. An empty or stale result
is unavailable evidence, never a zero reading and never an all-clear.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.egress import EgressResult, Outcome, egress
from app.geo import haversine_km
from app.models import AirQualityReading, Record, SourceId

HOST = "api.openaq.org"
ROOT = f"https://{HOST}/v3"
PM25_PARAMETER_ID = 2
MAX_LOCATIONS = 8


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "X-API-Key": settings.openaq_api_key or "replay",
    }


def _error(message: str, *, url: str = ROOT) -> EgressResult:
    return EgressResult(
        outcome=Outcome.UPSTREAM_ERROR,
        url=url,
        host=HOST,
        error=message,
    )


def _parse_datetime(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        value = str(raw).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _to_ug_m3(value: Any, unit: str | None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None

    normalized = (unit or "").lower().replace(" ", "")
    normalized = normalized.replace("μ", "u").replace("µ", "u").replace("³", "3")
    if normalized in {"ug/m3", "ugm-3", "ugm3"}:
        return number
    if normalized in {"mg/m3", "mgm-3", "mgm3"}:
        return number * 1000.0
    return None


def _pm25_sensors(location: dict[str, Any]) -> dict[int, str]:
    sensors: dict[int, str] = {}
    for sensor in location.get("sensors") or []:
        parameter = sensor.get("parameter") or {}
        if parameter.get("id") != PM25_PARAMETER_ID and str(
            parameter.get("name") or ""
        ).lower() not in {"pm25", "pm2.5"}:
            continue
        try:
            sensor_id = int(sensor["id"])
        except (KeyError, TypeError, ValueError):
            continue
        sensors[sensor_id] = str(parameter.get("units") or "")
    return sensors


async def get_pm25_near(
    lat: float,
    lon: float,
    *,
    radius_km: float | None = None,
) -> tuple[list[AirQualityReading], EgressResult]:
    """Return normalized PM2.5 readings near a point.

    Stale records are returned with `record.stale=True` so provenance remains
    visible, but callers must exclude them from assessments and routing.
    """
    if not settings.openaq_api_key and not getattr(settings, "replay", False):
        return [], _error("OPENAQ_API_KEY is not configured")

    radius = min(25.0, max(0.1, radius_km or settings.air_quality_station_radius_km))
    locations_url = f"{ROOT}/locations"
    locations_result = await egress.fetch(
        locations_url,
        params={
            "coordinates": f"{lat:.4f},{lon:.4f}",
            "radius": int(radius * 1000),
            "parameters_id": PM25_PARAMETER_ID,
            "mobile": "false",
            "limit": MAX_LOCATIONS,
        },
        headers=_headers(),
    )
    if not locations_result.ok:
        return [], locations_result

    payload = locations_result.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return [], _error("OpenAQ locations response was not valid JSON", url=locations_url)

    locations: list[tuple[int, dict[str, Any], dict[int, str]]] = []
    for location in payload["results"][:MAX_LOCATIONS]:
        sensors = _pm25_sensors(location)
        try:
            location_id = int(location.get("id"))
        except (TypeError, ValueError):
            continue
        if sensors:
            locations.append((location_id, location, sensors))

    if not locations:
        return [], locations_result

    async def latest(location_id: int) -> EgressResult:
        return await egress.fetch(
            f"{ROOT}/locations/{location_id}/latest",
            params={"limit": 100},
            headers=_headers(),
        )

    latest_results = await asyncio.gather(
        *(latest(location_id) for location_id, _, _ in locations)
    )

    readings: list[AirQualityReading] = []
    failed: EgressResult | None = None
    for (location_id, location, sensors), result in zip(locations, latest_results):
        if not result.ok:
            failed = failed or result
            continue
        latest_payload = result.json()
        if not isinstance(latest_payload, dict):
            failed = failed or _error(
                "OpenAQ latest response was not valid JSON", url=result.url
            )
            continue

        station_coords = location.get("coordinates") or {}
        for item in latest_payload.get("results") or []:
            try:
                sensor_id = int(item.get("sensorsId"))
            except (TypeError, ValueError):
                continue
            unit = sensors.get(sensor_id)
            if unit is None:
                continue
            value = _to_ug_m3(item.get("value"), unit)
            observed = _parse_datetime((item.get("datetime") or {}).get("utc"))
            coords = item.get("coordinates") or station_coords
            try:
                station_lat = float(coords.get("latitude"))
                station_lon = float(coords.get("longitude"))
            except (TypeError, ValueError):
                continue
            if value is None or observed is None:
                continue

            record = Record(
                record_id=f"openaq:{location_id}:{sensor_id}:{observed.isoformat()}",
                source_id=SourceId.OPENAQ,
                data_class=(
                    "replay" if result.outcome is Outcome.REPLAY else "official"
                ),
                observed_at=observed,
                ttl_seconds=settings.air_quality_ttl_seconds,
                provenance_url=f"{ROOT}/locations/{location_id}/latest",
                geometry={
                    "type": "Point",
                    "coordinates": [station_lon, station_lat],
                },
                payload={
                    "location_id": location_id,
                    "sensor_id": sensor_id,
                    "provider": (location.get("provider") or {}).get("name"),
                    "owner": (location.get("owner") or {}).get("name"),
                    "original_unit": unit,
                },
            )
            readings.append(
                AirQualityReading(
                    station_id=f"openaq:{location_id}",
                    station_name=location.get("name"),
                    lat=station_lat,
                    lon=station_lon,
                    pm25_ug_m3=value,
                    distance_km=haversine_km(lat, lon, station_lat, station_lon),
                    record=record,
                )
            )

    readings.sort(
        key=lambda reading: (
            reading.distance_km if reading.distance_km is not None else 1e9,
            -reading.record.observed_at.timestamp() if reading.record.observed_at else 0,
        )
    )
    if not readings and failed is not None:
        return [], failed
    return readings, locations_result
