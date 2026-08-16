from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import replay
from app.egress import EgressResult, Outcome
from app.sources import osrm


def test_only_rifle_club_queries_select_the_authored_scenario():
    assert replay.scenario_meta().get("name")
    assert replay.is_scenario_query("Rifle Club Road, Spokane County")
    assert replay.is_scenario_query("West Rifle-Club Court")
    assert not replay.is_scenario_query("404 N Havana St, Spokane")
    assert not replay.is_scenario_query("Nine Mile Falls School")


@pytest.mark.asyncio
async def test_live_osrm_route_can_bypass_replay(monkeypatch):
    seen = {}

    async def fake_fetch(url, *, params=None, bypass_replay=False, **kwargs):
        seen["bypass_replay"] = bypass_replay
        return EgressResult(
            outcome=Outcome.OK,
            url=url,
            host="router.project-osrm.org",
            status=200,
            body='{"code":"Ok","routes":[]}',
        )

    monkeypatch.setattr(osrm, "egress", SimpleNamespace(fetch=fake_fetch))
    await osrm.plan_routes((47.7, -117.5), (47.65, -117.3), bypass_replay=True)
    assert seen["bypass_replay"] is True
