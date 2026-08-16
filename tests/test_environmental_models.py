"""Contracts for air-quality and satellite hotspot evidence."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.models import (
    AirQualityAssessment,
    AirQualityReading,
    FireHotspot,
    Record,
    RouteCandidate,
    SourceId,
    utcnow,
)


def _record(source: SourceId, *, age_seconds: int = 0, ttl_seconds: int = 7200):
    return Record(
        record_id=f"{source.value.lower()}:test",
        source_id=source,
        observed_at=utcnow() - timedelta(seconds=age_seconds),
        ttl_seconds=ttl_seconds,
    )


def test_air_quality_requires_a_real_nonnegative_measurement():
    reading = AirQualityReading(
        station_id="station:1",
        lat=47.66,
        lon=-117.43,
        pm25_ug_m3=18.4,
        record=_record(SourceId.OPENAQ),
    )
    assert reading.pm25_ug_m3 == 18.4
    assert reading.usable is True

    with pytest.raises(ValidationError):
        AirQualityReading(
            station_id="station:1",
            lat=47.66,
            lon=-117.43,
            pm25_ug_m3=-1,
            record=_record(SourceId.OPENAQ),
        )


def test_stale_air_quality_is_not_usable():
    reading = AirQualityReading(
        station_id="station:old",
        lat=47.66,
        lon=-117.43,
        pm25_ug_m3=35.0,
        record=_record(SourceId.OPENAQ, age_seconds=7201),
    )
    assert reading.record.stale is True
    assert reading.usable is False


def test_unavailable_assessment_never_invents_zero_pm25():
    assessment = AirQualityAssessment()
    assert assessment.checked is False
    assert assessment.status == "unavailable"
    assert assessment.max_pm25 is None


def test_route_carries_the_requested_air_quality_contract():
    route = RouteCandidate(
        route_id="route-a",
        geometry={"type": "LineString", "coordinates": [[-117.5, 47.7], [-117.4, 47.6]]},
        distance_km=10,
        eta_min=15,
        record=_record(SourceId.OSRM),
        air_quality=AirQualityAssessment(
            checked=True,
            status="available",
            max_pm25=42.1,
            unhealthy_segment="route-a:segment-2",
            source=SourceId.OPENAQ,
            updated_at=utcnow(),
            station_count=2,
        ),
        air_quality_warning="Elevated PM2.5 along segment 2.",
    )
    payload = route.model_dump(mode="json")
    assert payload["air_quality"]["checked"] is True
    assert payload["air_quality"]["max_pm25"] == 42.1
    assert payload["air_quality_warning"]


def test_firms_hotspot_is_explicitly_a_point_detection():
    hotspot = FireHotspot(
        hotspot_id="firms:20260815:001",
        lat=47.8,
        lon=-117.6,
        acquired_at=utcnow(),
        satellite="NOAA-20",
        instrument="VIIRS",
        confidence="nominal",
        fire_radiative_power_mw=8.2,
        record=_record(SourceId.FIRMS),
    )
    assert hotspot.record.source_id is SourceId.FIRMS
    assert not hasattr(hotspot, "perimeter")


def test_air_quality_is_an_internal_enrichment_not_a_model_tool():
    from app.tools import TOOL_SCHEMAS

    names = {schema["function"]["name"] for schema in TOOL_SCHEMAS}
    assert "get_air_quality" not in names
