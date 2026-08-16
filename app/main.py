"""FastAPI application: plan, stream, monitor, health.

The SSE stream is what makes the agent's work visible. It carries observable
execution only — tool names, arguments, outcomes, timings, decisions — never
model deliberation (book section 8).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import monitor, replay, store, tools
from app.agent import agent
from app.config import REPO_ROOT, settings
from app.egress import Outcome, egress
from app.models import HouseholdNeeds, Place, Record, SourceId, SourceStatus, StepKind
from app.session import EvacuationSession, registry
from app.sources import mapbox

WEB_DIST = REPO_ROOT / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init()
    if settings.replay and settings.purge_demo_data_on_start:
        store.purge_all()
    yield
    for s in registry.all():
        await monitor.supervisor.stop(s.session_id)


app = FastAPI(title="Always-On Wildfire Evacuation Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- request models ----------------------------------------------------------


class ResolvedLocationInput(BaseModel):
    """Coordinates already selected from the address autocomplete result.

    The browser has just resolved these coordinates through the governed
    ``/api/geocode`` endpoint. Carrying them into ``/api/plan`` avoids a second
    geocoder request that can fail independently or resolve the same text
    differently.
    """

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    label: str = Field(min_length=1, max_length=240)
    source: Literal["MAPBOX", "REPLAY"] = "MAPBOX"


class PlanRequest(BaseModel):
    query: str = Field(..., description="Landmark or address")
    message: str | None = None
    needs: HouseholdNeeds = Field(default_factory=HouseholdNeeds)
    approved_contacts: list[str] = Field(default_factory=list)
    session_id: str | None = None
    location: ResolvedLocationInput | None = None


class MessageRequest(BaseModel):
    message: str


# --- endpoints ---------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """Everything a presenter needs to check before going on stage."""
    sandbox_ok = False
    sandbox_detail = "not probed"
    policy_enforced = False

    if not settings.replay:
        probe = await egress.fetch(
            "https://nominatim.openstreetmap.org/status.php", timeout=10.0
        )
        sandbox_ok = probe.outcome is Outcome.OK
        sandbox_detail = probe.error or f"HTTP {probe.status}"

    # The containment check: a host absent from the policy must still be
    # refused. This runs in both modes, because replay never fakes a denial.
    denial = await egress.fetch(
        "https://cameras.alertwildfire.org/api/firecams/v0/cameras",
        policy_probe=True,
        timeout=10.0,
    )
    policy_enforced = denial.outcome is Outcome.POLICY_DENIED
    if settings.replay:
        sandbox_ok = policy_enforced
        sandbox_detail = "probed via policy denial"

    nim_ok = False
    nim_detail = ""
    try:
        models = await agent.client.models.list()
        nim_ok = any(m.id == settings.inference_model for m in models.data)
        nim_detail = ", ".join(m.id for m in models.data) or "no models listed"
    except Exception as exc:
        nim_detail = str(exc)[:200]

    return {
        "mode": settings.data_mode,
        "replay": settings.replay,
        "scenario": replay.scenario_meta() if settings.replay else None,
        "phase": replay.get_phase(),
        "sandbox": {"ok": sandbox_ok, "detail": sandbox_detail, "name": settings.sandbox},
        "policy_enforced": policy_enforced,
        "policy_detail": denial.denial.summary if denial.denial else denial.error,
        "inference": {
            "ok": nim_ok,
            "model": settings.inference_model,
            "base_url": settings.inference_base_url,
            "detail": nim_detail,
        },
        "privacy": {
            "synthetic_only": settings.replay,
            "delivery": "simulated",
            "retention": (
                "cleared on process start"
                if settings.replay and settings.purge_demo_data_on_start
                else "persistent prototype storage"
            ),
        },
        "snapshots": store.snapshot_count(),
    }


@app.get("/api/scenario")
async def scenario() -> dict[str, Any]:
    return {
        "mode": settings.data_mode,
        "replay": settings.replay,
        "phase": replay.get_phase(),
        "scenario": replay.scenario_meta() if settings.replay else None,
    }


@app.get("/api/geocode")
async def geocode_suggestions(
    q: str = Query(min_length=3, max_length=120),
) -> dict[str, Any]:
    """Debounced address suggestions for the resident input.

    Results are temporary Mapbox lookups and are constrained to the product's
    Spokane County service area by the same source adapter used by the agent.
    """
    normalized = " ".join(q.split())
    if settings.replay:
        demo_results = replay.demo_location_suggestions(normalized, limit=5)
        if demo_results:
            return {"results": demo_results}

    places, result = await mapbox.search(normalized, limit=5)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error or "address search unavailable")
    return {
        "results": [
            {
                "id": place.record.record_id,
                "label": place.label,
                "lat": place.lat,
                "lon": place.lon,
                "source": "MAPBOX",
            }
            for place in places
        ]
    }


@app.post("/api/plan")
async def plan(req: PlanRequest) -> dict[str, Any]:
    session = registry.get(req.session_id) if req.session_id else None
    if session is None:
        session = registry.create()

    session.query = req.query
    session.needs = req.needs
    session.approved_contacts = list(req.approved_contacts)

    if req.location is not None:
        # The product's authoritative evacuation and shelter layers cover
        # Spokane County only. Never let a client-supplied coordinate bypass
        # that same service-area limit used by Mapbox search.
        west, south, east, north = (-118.20, 47.28, -116.85, 48.10)
        if not (west <= req.location.lon <= east and south <= req.location.lat <= north):
            raise HTTPException(422, "selected location is outside the Spokane service area")

        selected_source = (
            SourceId.MAPBOX if req.location.source == "MAPBOX" else SourceId.DERIVED
        )
        record = Record(
            record_id=f"selected:{session.session_id}",
            source_id=selected_source,
            data_class="derived" if req.location.source == "MAPBOX" else "replay",
            ttl_seconds=900,
            provenance_url=(
                "https://api.mapbox.com/search/geocode/v6/forward"
                if req.location.source == "MAPBOX"
                else "local replay address catalog"
            ),
            geometry={
                "type": "Point",
                "coordinates": [req.location.lon, req.location.lat],
            },
            payload={
                "input_method": "autocomplete_selection",
                "autocomplete_source": req.location.source,
            },
        )
        session.place = Place(
            lat=req.location.lat,
            lon=req.location.lon,
            label=req.location.label,
            record=record,
        )
        session.record_source(
            SourceStatus(
                source_id=selected_source,
                outcome="OK" if req.location.source == "MAPBOX" else "REPLAY",
                detail=(
                    "selected from governed address autocomplete"
                    if req.location.source == "MAPBOX"
                    else "selected from captured replay address catalog"
                ),
                record_count=1,
            )
        )

    message = req.message or (
        f"I'm near {req.query}. My household has {req.needs.describe()}. "
        "Do I need to leave, and where should I go?"
    )

    await agent.run(session, message)
    return session.snapshot()


@app.post("/api/session/{session_id}/message")
async def message(session_id: str, req: MessageRequest) -> dict[str, Any]:
    session = _require(session_id)
    await agent.run(session, req.message)
    return session.snapshot()


@app.get("/api/session/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    return _require(session_id).snapshot()


@app.get("/api/stream/{session_id}")
async def stream(session_id: str) -> StreamingResponse:
    session = _require(session_id)

    async def events():
        q = session.subscribe()
        try:
            # Replay what already happened so a late subscriber sees the whole
            # trace, then stream the rest.
            yield _sse({"type": "snapshot", "state": session.snapshot()})
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _sse(msg)
        finally:
            session.unsubscribe(q)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/session/{session_id}/monitor/start")
async def monitor_start(session_id: str) -> dict[str, Any]:
    session = _require(session_id)
    monitor.supervisor.start(session)
    return {"monitoring": True, "poll_seconds": monitor.POLL_SECONDS}


@app.post("/api/session/{session_id}/monitor/stop")
async def monitor_stop(session_id: str) -> dict[str, Any]:
    session = _require(session_id)
    await monitor.supervisor.stop(session_id)
    return {"monitoring": False}


@app.post("/api/session/{session_id}/monitor/check")
async def monitor_check(session_id: str) -> dict[str, Any]:
    """Run one pass now, instead of waiting for the interval."""
    session = _require(session_id)
    event = await monitor.run_once(session)
    return {"changed": event is not None, "event": event, "state": session.snapshot()}


@app.post("/api/demo/trigger-closure")
async def trigger_closure() -> dict[str, Any]:
    """Advance the replay scenario so a new closure appears at the source.

    This changes what the world looks like, not what the agent believes. The
    monitor still has to fetch, diff and decide on its own — which is why the
    replan that follows is a real recalculation.
    """
    if not settings.replay:
        raise HTTPException(
            400,
            "The simulated closure only exists in replay mode. In live mode the "
            "monitor is watching real WSDOT and SREC feeds.",
        )
    replay.set_phase("after")
    replay.clear_cache()
    return {"phase": "after", "note": "A new hard closure is now published by SREC."}


@app.post("/api/demo/reset")
async def reset_demo() -> dict[str, Any]:
    replay.set_phase("before")
    replay.clear_cache()
    return {"phase": "before"}


@app.post("/api/session/{session_id}/notify")
async def notify(session_id: str, req: MessageRequest) -> dict[str, Any]:
    """Direct notification attempt, used by the containment demonstration."""
    session = _require(session_id)
    result, ms = await tools.call(
        session, "send_notification", {"contact": req.message}
    )
    blocked = result.get("status") == "blocked"
    session.step(
        StepKind.BLOCKED if blocked else StepKind.TOOL,
        "send_notification",
        detail=result.get("reason") or result.get("message"),
        arguments={"contact": req.message},
        outcome="BLOCKED" if blocked else "sent",
        latency_ms=ms,
    )
    return {"result": result, "state": session.snapshot()}


def _require(session_id: str) -> EvacuationSession:
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(404, f"no session {session_id}")
    return session


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


# --- static SPA --------------------------------------------------------------

if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIST / "index.html")
