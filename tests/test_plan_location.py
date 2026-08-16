from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app import main
from app import replay


@pytest.mark.asyncio
async def test_plan_uses_coordinates_selected_by_address_autocomplete(monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(main.agent, "run", run)

    result = await main.plan(
        main.PlanRequest(
            query="8414 North Molly Street, Spokane, Washington",
            location=main.ResolvedLocationInput(
                lat=47.734881,
                lon=-117.470206,
                label="8414 North Molly Street, Spokane, Washington 99208, United States",
            ),
        )
    )

    assert result["place"]["lat"] == 47.734881
    assert result["place"]["lon"] == -117.470206
    assert result["place"]["record"]["source_id"] == "MAPBOX"
    assert (
        result["place"]["record"]["payload"]["input_method"]
        == "autocomplete_selection"
    )
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_plan_rejects_selected_coordinates_outside_service_area(monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(main.agent, "run", run)

    with pytest.raises(main.HTTPException) as exc:
        await main.plan(
            main.PlanRequest(
                query="Washington, DC",
                location=main.ResolvedLocationInput(
                    lat=38.9072,
                    lon=-77.0369,
                    label="Washington, District of Columbia, United States",
                ),
            )
        )

    assert exc.value.status_code == 422
    run.assert_not_awaited()


def test_replay_demo_address_matches_partial_input():
    results = replay.demo_location_suggestions("8414 north molly")

    assert len(results) == 1
    assert results[0]["lat"] == 47.734881
    assert results[0]["source"] == "REPLAY"
