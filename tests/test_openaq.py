from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.egress import EgressResult, Outcome
from app.sources import openaq


LOCATIONS = {
    "meta": {"found": 1},
    "results": [
        {
            "id": 101,
            "name": "Spokane Test Station",
            "coordinates": {"latitude": 47.66, "longitude": -117.43},
            "provider": {"name": "AirNow"},
            "owner": {"name": "Washington Ecology"},
            "sensors": [
                {
                    "id": 201,
                    "parameter": {"id": 2, "name": "pm25", "units": "µg/m³"},
                },
                {
                    "id": 202,
                    "parameter": {"id": 5, "name": "o3", "units": "ppm"},
                },
            ],
        }
    ],
}

LATEST = {
    "meta": {"found": 2},
    "results": [
        {
            "datetime": {"utc": "2026-08-15T19:30:00Z"},
            "value": 42.1,
            "coordinates": {"latitude": 47.66, "longitude": -117.43},
            "sensorsId": 201,
            "locationsId": 101,
        },
        {
            "datetime": {"utc": "2026-08-15T19:30:00Z"},
            "value": 0.03,
            "coordinates": {"latitude": 47.66, "longitude": -117.43},
            "sensorsId": 202,
            "locationsId": 101,
        },
    ],
}


def _settings(*, key="openshell:resolve:env:OPENAQ_API_KEY", ttl=7200):
    return SimpleNamespace(
        openaq_api_key=key,
        air_quality_station_radius_km=25.0,
        air_quality_ttl_seconds=ttl,
    )


@pytest.mark.asyncio
async def test_fetches_pm25_with_placeholder_header_and_provenance(monkeypatch):
    calls = []

    async def fake_fetch(url, *, params=None, headers=None, **kwargs):
        calls.append((url, params, headers))
        body = LOCATIONS if url.endswith("/locations") else LATEST
        return EgressResult(
            outcome=Outcome.OK,
            url=url,
            host=openaq.HOST,
            status=200,
            body=json.dumps(body),
        )

    monkeypatch.setattr(openaq, "settings", _settings(ttl=10**9))
    monkeypatch.setattr(openaq, "egress", SimpleNamespace(fetch=fake_fetch))

    readings, result = await openaq.get_pm25_near(47.65, -117.44)

    assert result.outcome is Outcome.OK
    assert len(readings) == 1
    assert readings[0].pm25_ug_m3 == 42.1
    assert readings[0].record.source_id.value == "OPENAQ"
    assert readings[0].record.provenance_url.endswith("/locations/101/latest")
    assert calls[0][1]["parameters_id"] == 2
    assert calls[0][2]["X-API-Key"] == "openshell:resolve:env:OPENAQ_API_KEY"
    assert all("OPENAQ_API_KEY" not in url for url, _, _ in calls)


@pytest.mark.asyncio
async def test_old_measurement_is_returned_only_as_stale_evidence(monkeypatch):
    async def fake_fetch(url, **kwargs):
        body = LOCATIONS if url.endswith("/locations") else LATEST
        return EgressResult(
            outcome=Outcome.OK,
            url=url,
            host=openaq.HOST,
            status=200,
            body=json.dumps(body),
        )

    monkeypatch.setattr(openaq, "settings", _settings(ttl=1))
    monkeypatch.setattr(openaq, "egress", SimpleNamespace(fetch=fake_fetch))

    readings, _ = await openaq.get_pm25_near(47.65, -117.44)

    assert len(readings) == 1
    assert readings[0].record.stale is True
    assert readings[0].usable is False


@pytest.mark.asyncio
async def test_no_nearby_station_is_empty_not_zero(monkeypatch):
    async def fake_fetch(url, **kwargs):
        return EgressResult(
            outcome=Outcome.OK,
            url=url,
            host=openaq.HOST,
            status=200,
            body='{"meta":{"found":0},"results":[]}',
        )

    monkeypatch.setattr(openaq, "settings", _settings())
    monkeypatch.setattr(openaq, "egress", SimpleNamespace(fetch=fake_fetch))

    readings, result = await openaq.get_pm25_near(47.65, -117.44)

    assert result.outcome is Outcome.OK
    assert readings == []


@pytest.mark.asyncio
async def test_missing_key_fails_without_attempting_network(monkeypatch):
    async def should_not_fetch(*args, **kwargs):
        raise AssertionError("network must not be called without a key")

    monkeypatch.setattr(openaq, "settings", _settings(key=""))
    monkeypatch.setattr(openaq, "egress", SimpleNamespace(fetch=should_not_fetch))

    readings, result = await openaq.get_pm25_near(47.65, -117.44)

    assert readings == []
    assert result.outcome is Outcome.UPSTREAM_ERROR
    assert "not configured" in result.error
