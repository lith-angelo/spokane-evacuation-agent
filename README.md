# Spokane Wildfire Evacuation Agent

An always-on evacuation assistant for Spokane County. A resident describes where
they are and who is with them; the agent returns their evacuation level, the
fires driving it, a shelter that meets their hard constraints, and a route
validated against known hazards — every safety-bearing answer carrying its
source and its observation time.

Then it keeps working. When a road closes, the monitor notices on its own,
invalidates the route it recommended, replans, and prepares a notification —
with no user message anywhere in that sequence.

The submitted runtime runs inside a NemoClaw/OpenShell sandbox where egress is
**deny-by-default**. Host execution remains a development option and is labelled
as not fully contained unless the health probe observes the policy boundary.
The complete list of hosts it may reach is `policies/spokane-evac.yaml`, GET/HEAD
only. Everything else fails closed with a real 403 from the L7 proxy, which the
agent surfaces as a blocked action rather than hiding.

**This is a prototype for Spark Hack Seattle.** It is not 911, not an official
evacuation order, and not certified navigation. Verify with official sources
before acting. Enter synthetic household/contact data only. Replay-mode session
data is cleared whenever the process starts, and notification delivery is
simulated.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
nemoclaw my-assistant policy-add --from-file ./policies/spokane-evac.yaml --yes
./scripts/run.sh                     # http://127.0.0.1:8811
```

Then `.venv/bin/python scripts/verify_demo.py` to walk the whole demo and check
every item on the acceptance checklist.

- `./scripts/run.sh` — replay scenario, single port, built UI
- `./scripts/run.sh --live` — live data through the sandbox
- `./scripts/run.sh --dev` — vite dev server on :5173 with hot reload

To present it, follow [docs/DEMO.md](docs/DEMO.md).

## What makes it more than an API wrapper

**The model never decides safety.** It parses intent, picks tools, and writes
prose. Point-in-polygon, level comparison, hard-constraint filtering, staleness,
route validation and the re-entry decision are deterministic code in
`app/safety.py`, which owns the final verdict. The model's text is rendered
*around* that verdict and is stored in a separate field.

**Unknown is not safe.** Absence of an evacuation polygon is not an All Clear. A
stale record may be shown but may never lower a level or clear a road. A shelter
capability the source does not affirm is treated as absent — including when the
source affirms its *negation*, which is why "NO PETS — service animals only"
does not read as a pet policy.

**Cross-source verification between real authorities.** SREC declares the
evacuation level; NIFC WFIGS supplies fire geometry. When a perimeter covers an
address that has no published zone, that is a reportable conflict, the
conservative reading wins, and confidence drops. Two views of one table would
have been easier and would have meant nothing.

**The sandbox boundary is a product feature.** `app/sources/firecam.py` is a real
capability against a real host that the policy omits. There is no simulated
failure branch anywhere in it.

## Architecture

```text
browser (web/, React + Leaflet)
        │  HTTP + SSE step trace
        ▼
NemoClaw/OpenShell sandbox
  FastAPI app/main.py ──────► app/agent.py ── OpenAI-compatible ──► local NIM (GN100)
  /api/plan  /api/stream            │                NVIDIA Nemotron 3.5 Lightning
  /api/health /api/session          │ tool calls
                                    ▼
                              app/tools.py ──► app/monitor.py  (always-on loop)
                                    │
              ┌─────────────────────┼──────────────────────┐
              ▼                     ▼                      ▼
        app/sources/*          app/geo.py            app/safety.py
              │              (shapely; owns          (owns the final
              │               no decisions)           verdict)
              ▼
        app/egress.py   ← the only module that touches the network
              │
       direct child curl (still inside the sandbox)
              │
       OpenShell L7 proxy   ← enforces policies/spokane-evac.yaml
              │
   services3.arcgis.com (NIFC WFIGS · SREC) · data.wsdot.wa.gov
   · nominatim.openstreetmap.org · router.project-osrm.org
   · api.mapbox.com (optional live location)
```

`EVAC_DATA_MODE=replay` serves fixtures **at the egress layer**, so every parser,
geometry operation and safety gate above it runs the same code it runs live.
With `EVAC_LIVE_LOCATION_IN_REPLAY=1`, Mapbox geocoding and OSRM routing may run
live for non-scenario addresses while hazard feeds remain fixture-backed for a
deterministic demo.

## Data sources

NIFC WFIGS (incidents, perimeters) · SREC (evacuation zones, shelters, local
closures) · WSDOT (highway closures) · Mapbox or Nominatim (geocoding) · OSRM
(routing). Verified URL shapes, the two distinct policy-denial
signatures, and two proxy quirks that constrain URL construction are in
[docs/SOURCES.md](docs/SOURCES.md).

Genasys Protect was removed from the policy: every `/api/**` route it publishes
returns an SPA shell rather than JSON. Keeping a standing grant to a host we
cannot use would have made the allowlist dishonest.

## Documentation

- [docs/DEMO.md](docs/DEMO.md) — the 3–5 minute script, both modes, and the
  questions you will be asked
- [docs/SOURCES.md](docs/SOURCES.md) — verified endpoints, egress signatures,
  resolved open decisions
- [docs/DESIGN.md](docs/DESIGN.md) — architecture, data contract, safety rules
- [docs/THIRD_PARTY_NOTICES.md](docs/THIRD_PARTY_NOTICES.md) — pinned dependency,
  license, and open-source-age evidence
- [docs/PLAN.md](docs/PLAN.md) — the milestones this was built against

## Tests

```bash
.venv/bin/python -m pytest -q            # 72 tests, no network, no sandbox
.venv/bin/python scripts/verify_demo.py  # end-to-end against a running server
```

`tests/test_safety.py` pairs every gate with a negative control — a scenario
where the unsafe answer is the tempting one. Each control has been verified to
fail when its gate is removed, so they are known to bite rather than merely to
pass.

## Rebuilding the replay scenario

```bash
EVAC_DATA_MODE=live .venv/bin/python scripts/build_fixtures.py
```

Captured fixtures are real bytes from the live sources. Authored fixtures cover
only the evacuation overlay, which is genuinely empty when no evacuation is
running. Each file records which it is in `_meta.origin`, and the UI labels every
replayed source.

## Not built

Turn-by-turn navigation. Any write to a government system. Real notification
delivery. Authentication, roles, audit, retention, monitoring — all required for
production, none present here.
