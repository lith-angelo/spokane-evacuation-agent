from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.egress import EgressResult, Outcome
from app.models import Incident, Record, SourceId, utcnow
from app.sources import firms


def _settings(*, key="openshell:resolve:env:FIRMS_MAP_KEY", ttl=21600):
    return SimpleNamespace(
        firms_map_key=key,
        firms_hotspot_ttl_seconds=ttl,
    )


def _csv(*, age_hours: float = 0.0) -> str:
    acquired = utcnow() - timedelta(hours=age_hours)
    date = acquired.strftime("%Y-%m-%d")
    time = acquired.strftime("%H%M")
    return "\n".join(
        [
            "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,frp,daynight",
            f"47.6600,-117.4300,334.1,0.4,0.4,{date},{time},N20,VIIRS,n,2.0NRT,12.7,D",
            # Inside the requested bounding box but outside its true 10 km radius.
            f"47.7390,-117.3090,320.0,0.4,0.4,{date},{time},N20,VIIRS,l,2.0NRT,3.1,D",
        ]
    )


@pytest.mark.asyncio
async def test_fetches_and_normalizes_viirs_hotspots_without_leaking_key(monkeypatch):
    calls = []

    async def fake_fetch(url, **kwargs):
        calls.append(url)
        return EgressResult(
            outcome=Outcome.OK,
            url=url.replace("test-secret", "[REDACTED]"),
            host=firms.HOST,
            status=200,
            body=_csv(),
        )

    monkeypatch.setattr(firms, "settings", _settings(key="test-secret"))
    monkeypatch.setattr(firms, "egress", SimpleNamespace(fetch=fake_fetch))

    hotspots, result = await firms.get_hotspots(47.65, -117.44, radius_km=10)

    assert result.outcome is Outcome.OK
    assert len(hotspots) == 1
    hotspot = hotspots[0]
    assert hotspot.record.source_id is SourceId.FIRMS
    assert hotspot.fire_radiative_power_mw == 12.7
    assert hotspot.brightness_k == 334.1
    assert hotspot.confidence == "nominal"
    assert hotspot.record.geometry["type"] == "Point"
    assert "test-secret" not in hotspot.record.provenance_url
    assert "[REDACTED]" in hotspot.record.provenance_url
    assert "/VIIRS_NOAA20_NRT/" in calls[0]


@pytest.mark.asyncio
async def test_old_detection_is_retained_only_as_stale_evidence(monkeypatch):
    async def fake_fetch(url, **kwargs):
        return EgressResult(
            outcome=Outcome.OK,
            url=url.replace("test-secret", "[REDACTED]"),
            host=firms.HOST,
            status=200,
            body=_csv(age_hours=8),
        )

    monkeypatch.setattr(firms, "settings", _settings(key="test-secret"))
    monkeypatch.setattr(firms, "egress", SimpleNamespace(fetch=fake_fetch))

    hotspots, _ = await firms.get_hotspots(47.65, -117.44, radius_km=10)

    assert len(hotspots) == 1
    assert hotspots[0].record.stale is True


@pytest.mark.asyncio
async def test_invalid_firms_payload_is_a_source_error(monkeypatch):
    async def fake_fetch(url, **kwargs):
        return EgressResult(
            outcome=Outcome.OK,
            url=url.replace("test-secret", "[REDACTED]"),
            host=firms.HOST,
            status=200,
            body="Invalid MAP_KEY.",
        )

    monkeypatch.setattr(firms, "settings", _settings(key="test-secret"))
    monkeypatch.setattr(firms, "egress", SimpleNamespace(fetch=fake_fetch))

    hotspots, result = await firms.get_hotspots(47.65, -117.44)

    assert hotspots == []
    assert result.outcome is Outcome.UPSTREAM_ERROR
    assert "rejected" in result.error
    assert "test-secret" not in result.url


@pytest.mark.asyncio
async def test_missing_map_key_fails_without_attempting_network(monkeypatch):
    async def should_not_fetch(*args, **kwargs):
        raise AssertionError("network must not be called without a MAP_KEY")

    monkeypatch.setattr(firms, "settings", _settings(key=""))
    monkeypatch.setattr(firms, "egress", SimpleNamespace(fetch=should_not_fetch))

    hotspots, result = await firms.get_hotspots(47.65, -117.44)

    assert hotspots == []
    assert result.outcome is Outcome.UPSTREAM_ERROR
    assert "not configured" in result.error


def test_association_does_not_promote_hotspot_to_incident():
    acquired = utcnow()
    hotspot_record = Record(
        record_id="firms:test",
        source_id=SourceId.FIRMS,
        observed_at=acquired,
    )
    hotspot = firms.FireHotspot(
        hotspot_id="firms:test",
        lat=47.66,
        lon=-117.43,
        acquired_at=acquired,
        record=hotspot_record,
    )
    incident = Incident(
        incident_id="wfigs:test",
        name="Test Fire",
        lat=47.665,
        lon=-117.435,
        record=Record(
            record_id="wfigs:test",
            source_id=SourceId.WFIGS,
            observed_at=acquired,
        ),
    )

    associated, distance = firms.nearest_incident(hotspot, [incident])

    assert associated is incident
    assert distance is not None and distance < 1
    assert hotspot.record.source_id is SourceId.FIRMS
    assert not hasattr(hotspot, "perimeter")
