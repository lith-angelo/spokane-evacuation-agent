# Implementation Plan

Companion to [DESIGN.md](./DESIGN.md). Milestones are ordered so that every one
ends in something demonstrable; if the clock runs out mid-plan, whatever is
finished still shows on screen.

Current state: `app/config.py` and `policies/spokane-evac.yaml` exist and are
untracked. Nothing else is built. The `my-assistant` sandbox is up but the
`spokane-evac` policy is **not yet applied** (`nemoclaw list` shows
`policies: npm, pypi, huggingface, brew, local-inference, openclaw-pricing`).

## M0 — Ground the environment (blocks everything)

1. Apply the network policy:
   ```bash
   nemoclaw my-assistant policy-add --from-file ./policies/spokane-evac.yaml --dry-run
   nemoclaw my-assistant policy-add --from-file ./policies/spokane-evac.yaml --yes
   nemoclaw my-assistant policy-list
   ```
2. Probe each of the six allowed hosts through the sandbox and record the
   working URL shapes into `docs/SOURCES.md` — particularly Genasys, whose
   routes are undocumented (DESIGN §9.3).
3. Confirm one host stays denied: `cameras.alertwildfire.org` must still 403.
4. Confirm the NIM answers at `EVAC_INFERENCE_BASE_URL` with an OpenAI-style
   `/chat/completions` and supports tool calls.
5. Resolve DESIGN §9.1 and §9.2 (SREC vs. Genasys, county GIS host). If SREC is
   adopted, amend the policy and re-apply here — not later.

**Done when:** each intended source has one known-good URL, one host is proven
denied, and the model returns a tool call.

## M1 — Egress spine

Files: `app/egress.py`, `tests/test_egress.py`

- `fetch(host, path, params, *, method="GET")` → `EgressResult` with the five
  outcomes of DESIGN §6.
- Allowlist check in process before dispatch; refuse unlisted hosts without
  spawning anything.
- Subprocess `nemoclaw <sandbox> exec --timeout … -- curl -sS -w …`, semaphore
  at `EVAC_EGRESS_CONCURRENCY`, serial 1 rps lane for Nominatim, identifying
  `User-Agent` from settings.
- Classify: exit 56 + `CONNECT tunnel failed, response 403` ⇒ `POLICY_DENIED`;
  `nemoclaw` failure ⇒ `SANDBOX_UNAVAILABLE`; non-2xx ⇒ `UPSTREAM_ERROR`.
- Tests cover classification from captured stdout/stderr fixtures — no network.

**Done when:** a denied host and an allowed host both produce the right outcome
against the real sandbox, and the unit tests pass without it.

## M2 — Records and sources

Files: `app/models.py`, `app/geo.py`, `app/sources/*.py`, `app/store.py`,
`app/replay.py`, fixtures under `tests/fixtures/`

- `models.py`: the provenance envelope (DESIGN §5) plus `EvacZone`, `Incident`,
  `Shelter`, `Closure`, `RouteCandidate`, `HouseholdNeeds`.
- Each source module: build URL, call `egress`, normalize into envelopes, set
  `ttl_seconds`, never raise on upstream failure — return the failure as data.
- `geo.py`: point-in-polygon, perimeter buffer, route ∩ hazard, haversine.
- `store.py`: SQLite at `EVAC_DB_PATH` — `sessions`, `steps`, `snapshots`.
  Snapshots append-only, keyed by `(source_id, fetched_at)`.
- `replay.py`: serve the M0-captured responses as fixtures so `EVAC_DATA_MODE=replay`
  exercises the identical parsing path.

**Done when:** `python -m app.sources.wfigs --lat … --lon …` style smoke checks
return normalized envelopes in both live and replay mode.

## M3 — Deterministic safety layer

Files: `app/safety.py`, `tests/test_safety.py`

Implement the six gates of DESIGN §7 as pure functions over M2 records — no
network, no model. Port the previous MVP's negative-control tests: a dangerous
route, an ignored Level 3, a violated accessibility constraint, a stale-data
downgrade, and a re-entry suggestion without an all-clear must each fail loudly.

**Done when:** every gate has a passing test *and* a failing negative control.

## M4 — Agent loop

Files: `app/agent.py`, `app/tools.py`, `tests/test_agent.py`

- Tool schemas: `geocode`, `get_evacuation_status`, `get_active_incidents`,
  `find_shelters`, `get_closures`, `plan_route`, `get_fire_camera` (the blocked
  one).
- Loop against the NIM with tool calling; cap the turns; record every step with
  `kind: model | tool | safety guard`, arguments, outcome, and latency.
- After the loop, `safety.py` produces the verdict. The model's prose is
  rendered *around* that verdict, never in place of it.
- If the model omits a required lookup (evacuation status, incidents), the
  deterministic layer fills it in and the trace says so.

**Done when:** a scripted request produces a full step trace ending in a
guard-owned verdict, with a stubbed model in tests.

## M5 — API and UI

Files: `app/main.py`, `web/`

- `POST /api/plan` (full result), `GET /api/stream` (SSE step trace),
  `GET /api/health` (sandbox reachable, policy applied, NIM reachable, mode).
- `web/`: location + household needs input, map with zones/perimeters/route,
  the answer, a sources panel showing `source_id` / `as of` / `stale`, and a
  step panel that renders blocked actions in their own color.

**Done when:** the browser flow runs end to end against live data.

## M6 — Demo script

File: `docs/DEMO.md`

1. Resident near Rifle Club Road, pets + mobility. Live Level lookup, fires,
   shelter meeting both constraints, route avoiding closures.
2. The agent reaches for the fire camera; OpenShell returns 403; the UI shows a
   blocked action naming the host. Show `nemoclaw my-assistant policy-list` to
   prove the boundary is external.
3. A source conflict or a stale record — show that the answer stays conservative
   and says what it does not know.
4. Ask about going home. No all-clear ⇒ refusal to recommend re-entry.

Include the fallback: `EVAC_DATA_MODE=replay` if the venue network fails.

## M7 — Hardening, if time allows

Sequenced by value: response caching keyed on the envelope TTL; retry with
backoff for `UPSTREAM_ERROR` only (never for `POLICY_DENIED`); porting the
40-case evaluation suite from `origin/codex/spokane-evacuation-mvp` onto the live
sources; narrowing the Genasys `/api/**` grant to the routes actually used.

## Risks

| Risk | Mitigation |
|---|---|
| Genasys routes turn out to be unusable | SREC FeatureServer as the evacuation source (DESIGN §9.1); decide at M0, not at M5 |
| OSRM demo server slow or down | Route step degrades to "route unavailable", never to a fabricated route; replay fixture as backup |
| Venue network blocks the sandbox entirely | `EVAC_DATA_MODE=replay`, with the mode stated on screen |
| Subprocess-per-fetch too slow under a live demo | Concurrency cap plus caching (M7); parallelize the independent tools in M4 |
| Model ignores a required tool | Deterministic backfill in M4; the guard runs regardless |

## Verification

```bash
python -m pytest -q                                   # M1–M4
nemoclaw my-assistant policy-list                     # policy applied
curl -s localhost:8000/api/health | jq                # sandbox + NIM + mode
```

The evaluation score, if M7's suite is ported, is a deterministic baseline
against a synthetic regression set — not a production accuracy claim and not a
safety guarantee. Say so wherever it is shown.
