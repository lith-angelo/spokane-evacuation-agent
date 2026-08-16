from __future__ import annotations

import pytest

from app import tools
from app.egress import EgressResult, Outcome
from app.models import FireHotspot, Incident, Place, Record, SourceId, utcnow
from app.session import EvacuationSession


def _result(source_host: str) -> EgressResult:
    return EgressResult(
        outcome=Outcome.OK,
        url=f"https://{source_host}/fixture",
        host=source_host,
        status=200,
    )


@pytest.mark.asyncio
async def test_active_incidents_keeps_firms_detections_separate(monkeypatch):
    now = utcnow()
    incident = Incident(
        incident_id="wfigs:1",
        name="Test Fire",
        lat=47.665,
        lon=-117.435,
        distance_km=2.0,
        record=Record(
            record_id="wfigs:1",
            source_id=SourceId.WFIGS,
            observed_at=now,
        ),
    )
    hotspot = FireHotspot(
        hotspot_id="firms:1",
        lat=47.66,
        lon=-117.43,
        acquired_at=now,
        distance_km=1.0,
        fire_radiative_power_mw=12.7,
        record=Record(
            record_id="firms:1",
            source_id=SourceId.FIRMS,
            observed_at=now,
        ),
    )

    async def fake_wfigs(*args, **kwargs):
        result = _result("services3.arcgis.com")
        return [incident], result, result

    async def fake_firms(*args, **kwargs):
        return [hotspot], _result("firms.modaps.eosdis.nasa.gov")

    monkeypatch.setattr(tools.wfigs, "get_active_incidents", fake_wfigs)
    monkeypatch.setattr(tools.firms, "get_hotspots", fake_firms)
    session = EvacuationSession(place=Place(lat=47.65, lon=-117.44))

    payload = await tools.get_active_incidents(session, radius_km=20)

    assert payload["count"] == 1
    assert payload["satellite_detection_count"] == 1
    assert payload["satellite_detections"][0]["associated_incident"] == "Test Fire"
    assert payload["satellite_detections"][0]["source"] == "FIRMS"
    assert session.incidents == [incident]
    assert session.fire_hotspots == [hotspot]
    assert {status.source_id for status in session.sources} == {
        SourceId.WFIGS,
        SourceId.FIRMS,
    }
