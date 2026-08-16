from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.egress import EgressResult, Outcome
from app.models import SourceId
from app.sources import mapbox


@pytest.mark.asyncio
async def test_geocode_parses_mapbox_feature(monkeypatch):
    monkeypatch.setattr(
        mapbox,
        "settings",
        SimpleNamespace(mapbox_access_token="test-token", live_location_in_replay=True),
    )

    captured = {}

    async def fake_fetch(*args, **kwargs):
        captured.update(kwargs)
        return EgressResult(
            outcome=Outcome.OK,
            url="https://api.mapbox.com/search/geocode/v6/forward",
            host="api.mapbox.com",
            status=200,
            body=json.dumps(
                {
                    "features": [
                        {
                            "id": "address.test",
                            "geometry": {"type": "Point", "coordinates": [-117.4, 47.7]},
                            "properties": {"full_address": "123 Test St, Spokane, WA"},
                        }
                    ]
                }
            ),
        )

    monkeypatch.setattr(mapbox.egress, "fetch", fake_fetch)
    place, result = await mapbox.geocode("123 Test St")

    assert result.outcome is Outcome.OK
    assert place is not None
    assert (place.lat, place.lon) == (47.7, -117.4)
    assert place.record.source_id is SourceId.MAPBOX
    assert captured["params"]["bbox"] == "-118.2,47.28,-116.85,48.1"
    assert captured["params"]["limit"] == 1


@pytest.mark.asyncio
async def test_search_returns_multiple_spokane_suggestions(monkeypatch):
    monkeypatch.setattr(
        mapbox,
        "settings",
        SimpleNamespace(mapbox_access_token="test-token", live_location_in_replay=True),
    )

    async def fake_fetch(*args, **kwargs):
        return EgressResult(
            outcome=Outcome.OK,
            url="https://api.mapbox.com/search/geocode/v6/forward",
            host="api.mapbox.com",
            status=200,
            body=json.dumps(
                {
                    "features": [
                        {
                            "id": "street.one",
                            "geometry": {"type": "Point", "coordinates": [-117.49, 47.72]},
                            "properties": {"name": "West Rifle Club Road"},
                        },
                        {
                            "id": "street.two",
                            "geometry": {"type": "Point", "coordinates": [-117.47, 47.73]},
                            "properties": {"name": "North Molly Street"},
                        },
                    ]
                }
            ),
        )

    monkeypatch.setattr(mapbox.egress, "fetch", fake_fetch)
    places, result = await mapbox.search("Rifle", limit=5)

    assert result.outcome is Outcome.OK
    assert [place.label for place in places] == ["West Rifle Club Road", "North Molly Street"]


@pytest.mark.asyncio
async def test_routes_parse_mapbox_geojson(monkeypatch):
    monkeypatch.setattr(
        mapbox,
        "settings",
        SimpleNamespace(mapbox_access_token="test-token", live_location_in_replay=True),
    )

    async def fake_fetch(*args, **kwargs):
        return EgressResult(
            outcome=Outcome.OK,
            url="https://api.mapbox.com/directions/v5/mapbox/driving-traffic/test",
            host="api.mapbox.com",
            status=200,
            body=json.dumps(
                {
                    "code": "Ok",
                    "routes": [
                        {
                            "distance": 12000,
                            "duration": 900,
                            "weight_name": "auto",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[-117.4, 47.7], [-117.3, 47.6]],
                            },
                        }
                    ],
                }
            ),
        )

    monkeypatch.setattr(mapbox.egress, "fetch", fake_fetch)
    routes, result = await mapbox.plan_routes((47.7, -117.4), (47.6, -117.3))

    assert result.outcome is Outcome.OK
    assert len(routes) == 1
    assert routes[0].distance_km == 12.0
    assert routes[0].record.source_id is SourceId.MAPBOX


@pytest.mark.asyncio
async def test_routes_reject_cross_country_endpoints(monkeypatch):
    monkeypatch.setattr(
        mapbox,
        "settings",
        SimpleNamespace(mapbox_access_token="test-token", live_location_in_replay=True),
    )

    routes, result = await mapbox.plan_routes((39.63141, -79.86353), (47.6553, -117.2764))

    assert routes == []
    assert result.outcome is Outcome.UPSTREAM_ERROR
    assert "outside the Spokane evacuation service area" in (result.error or "")
