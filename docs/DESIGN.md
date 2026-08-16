# Design — Always-On Wildfire Evacuation Agent (Spokane)

Status: **built and running.** All milestones M0–M7 in [PLAN.md](./PLAN.md) are
complete. Every open decision in §9 is resolved — see
[SOURCES.md](./SOURCES.md) for what the probing found and
[DEMO.md](./DEMO.md) for how to present it.

Scope: hackathon prototype (Spark Hack Seattle). Not 911, not an official
evacuation order, not certified navigation.

Two things below were written before the sources were probed and turned out to
be wrong in the detail; both are corrected in §9 and in SOURCES.md. The egress
contract in §6 lists four outcomes but the proxy produces **two distinct denial
shapes**, and Genasys Protect is **not usable** as an evacuation source.

## 1. What it is

A resident asks, in plain language, "I'm near Rifle Club Road, I have a dog and
my mother uses a walker — do I need to leave, and where do I go?" The agent
answers with the evacuation level for that point, the fires that drive it, a
shelter that actually meets the household's hard constraints, and a candidate
route that avoids known closures — every safety-bearing field carrying its
source and its observation time.

It runs as an always-on assistant inside a NemoClaw/OpenShell sandbox where
network egress is **deny-by-default**. The set of hosts it can reach is exactly
`policies/spokane-evac.yaml`. Anything else fails closed with a real 403 from
the L7 proxy.

### 1.1 Relationship to the previous MVP

`origin/codex/spokane-evacuation-mvp` is the earlier build: FastAPI + React,
deterministic orchestration, replayed snapshots plus labeled synthetic data,
DeepSeek as a stopgap model provider. It works offline and is the fallback demo.

This design replaces three things in it:

| Previous MVP | This design |
|---|---|
| Replay snapshots baked into the repo | Live public APIs, fetched at request time; replay kept as an explicit mode |
| Network access unconstrained by anything but code | Egress enumerated in policy and enforced by OpenShell outside the agent's reach |
| DeepSeek / remote key | Local NIM (`nvidia/Qwen3.6-35B-A3B-NVFP4`) over an OpenAI-compatible endpoint |

Two things carry over unchanged, because they were the previous build's real
contribution: the **provenance envelope** on every record and the
**deterministic safety guard** that owns the final verdict.

## 2. Principles

1. **The sandbox boundary is a product feature, not plumbing.** A capability the
   policy forbids must surface to the user as a blocked action with the host and
   the reason — never as a silent gap, never as a fabricated answer.
2. **Unknown is not safe.** Absence of an evacuation polygon is not an All
   Clear. Stale data is not evidence of safety. Missing shelter capability is
   not "accessible".
3. **The model never decides safety.** The LLM parses intent, chooses tools, and
   writes prose. Point-in-polygon, level comparison, hard-constraint filtering,
   staleness, and the re-entry decision are deterministic code.
4. **No fallback around a denial.** If policy denies a host, the request fails.
   `ALLOW_HOST_DIRECT_FALLBACK` exists only for the case where the *sandbox
   itself* is unreachable, is off in the demo, and re-applies `ALLOWED_HOSTS` in
   process when on.
5. **Snapshots, not overwrites.** Each fetch is archived. Conflicts between
   sources are shown, and the conservative reading wins.

## 3. Runtime topology

```text
browser (web/, static SPA)
        │  HTTP + SSE
        ▼
FastAPI  app/main.py  ──────────────► app/agent.py ── OpenAI-compatible ──► local NIM
  /api/plan  /api/stream  /api/health        │            (EVAC_INFERENCE_BASE_URL)
                                             │ tool calls
                                             ▼
                                        app/tools.py
                                             │
                        ┌────────────────────┼────────────────────┐
                        ▼                    ▼                    ▼
                  app/sources/*        app/geo.py           app/safety.py
                        │            (shapely, ownsdeterministic
                        │             point-in-polygon)    final verdict)
                        ▼
                   app/egress.py   ← the only module that touches the network
                        │
             nemoclaw <sandbox> exec -- curl …
                        │
                 OpenShell L7 proxy   ← enforces policies/spokane-evac.yaml
                        │
        services3.arcgis.com · protect.genasys.com · data.wsdot.wa.gov
        nominatim.openstreetmap.org · router.project-osrm.org · api.mapbox.com
```

The FastAPI process runs on the host; its outbound calls are funnelled through
the sandbox so that the sandbox's policy governs them. This is deliberate: it
means the demo's egress story is enforced by something the agent cannot edit.

## 4. Module map

Everything under `app/` except `config.py` is still to be written.

| Module | Responsibility | Notes |
|---|---|---|
| `config.py` | Settings from env, `ALLOWED_HOSTS` mirror | **Done** |
| `egress.py` | The only network path. Builds `nemoclaw exec -- curl` invocations, bounded by `EVAC_EGRESS_CONCURRENCY`, classifies every outcome | See §6 |
| `models.py` | Pydantic records + the provenance envelope | See §5 |
| `sources/wfigs.py` | NIFC incident locations + perimeters | `services3.arcgis.com/T4QMspbfLg3qTGWY/...` |
| `sources/genasys.py` | Evacuation zones and Level 1/2/3 | `protect.genasys.com/api/**` |
| `sources/wsdot.py` | State highway closures and alerts | `data.wsdot.wa.gov/arcgis/rest/**` |
| `sources/nominatim.py` | Landmark/address → coordinates | 1 req/s, identifying User-Agent required |
| `sources/osrm.py` | Candidate routes with alternatives | Demo server, no SLA |
| `sources/firecam.py` | **Deliberately blocked.** `cameras.alertwildfire.org` is absent from the policy | See §8 |
| `geo.py` | Point-in-polygon, buffers, distance, closure intersection | shapely |
| `safety.py` | Hard gates; owns the final resident-facing verdict | See §7 |
| `agent.py` | Tool-calling loop against the NIM, step trace | Model never writes a verdict field |
| `tools.py` | Tool schemas exposed to the model | One per capability, thin over `sources/` |
| `store.py` | SQLite at `EVAC_DB_PATH`: sessions, step traces, fetch snapshots | Snapshots are append-only |
| `replay.py` | Fixture-backed responses when `EVAC_DATA_MODE=replay` | Same envelope, `data_class=replay` |
| `main.py` | FastAPI app, SSE step stream, static `web/` mount | |

`web/` is a small SPA: input (location, household needs, time), a map, the
answer, a sources panel, and a live agent-step panel. `.gitignore` already
anticipates `web/node_modules` and `web/dist`.

## 5. Data contract

Every record that can influence a safety decision is wrapped:

```json
{
  "record_id": "wfigs:2026-WANES-001845:perimeter",
  "source_id": "WFIGS | GENASYS | SPOKANE_GIS | WSDOT | NOMINATIM | OSRM",
  "data_class": "official | derived | replay | synthetic",
  "authority_tier": 1,
  "observed_at": "2026-08-15T06:12:00Z",
  "fetched_at":  "2026-08-15T06:40:11Z",
  "valid_from": null,
  "valid_to": null,
  "stale": false,
  "ttl_seconds": 900,
  "provenance_url": "https://…",
  "geometry": null,
  "payload": {}
}
```

Rules on this envelope:

- `stale` is computed from `fetched_at + ttl_seconds`, not asserted by a source.
- A `stale` record may be *shown* but may never be the basis of a downgrade or a
  re-entry recommendation.
- Conflicting records from same-tier sources are both retained. For
  safety-bearing fields the conservative value is used and the conflict is
  displayed.
- Numbers that get revised (acreage, containment, structures lost) are always
  rendered as "value, source, as-of date". No permanent truth is claimed.

## 6. Egress contract

`app/egress.py` is the single choke point. Every call returns one of five
outcomes, and each maps to distinct user-facing language:

| Outcome | Trigger | Agent behavior |
|---|---|---|
| `OK` | 2xx from upstream | Normal |
| `POLICY_DENIED` | `curl` exit 56 with `CONNECT tunnel failed, response 403`, or host not in `ALLOWED_HOSTS` | Report the capability as **blocked**, name the host, continue with remaining tools |
| `UPSTREAM_ERROR` | non-2xx, timeout, malformed body | Report the source as **unavailable**; never treat as "no hazard" |
| `SANDBOX_UNAVAILABLE` | `nemoclaw exec` itself fails | Degraded banner; host-direct only if `ALLOW_HOST_DIRECT_FALLBACK=1`, and then still allowlisted in process |
| `REPLAY` | `EVAC_DATA_MODE=replay` | Fixture, labeled `data_class=replay` in the sources panel |

Verified against the live sandbox (policy not yet applied, hence the denial):

```console
$ nemoclaw my-assistant exec -- curl -sS -o /dev/null -w 'HTTP %{http_code}\n' \
    https://nominatim.openstreetmap.org/status.php
curl: (56) CONNECT tunnel failed, response 403
HTTP 000
nemoclaw: recent network policy denial detected for nominatim.openstreetmap.org:443 …
```

Note the shape: the tool sees `HTTP 000` and exit 56 — the 403 is on the tunnel,
not the response. Classification must key on exit code 56 plus the CONNECT
message, not on an HTTP status. `nemoclaw`'s own hint goes to stderr and should
be captured but not parsed as data.

Concurrency is capped at `EVAC_EGRESS_CONCURRENCY` (default 8) because each call
is a subprocess, and Nominatim additionally gets a 1 req/s serial lane.

## 7. Safety rules

`app/safety.py` runs after the tools and before anything reaches the resident.
Any gate failing zeroes the recommendation and forces the conservative branch.

1. **Level 3 is immediate.** Issue "leave now" first; do not wait on shelter or
   route work. But do not *skip* the hard-constraint search either — run it in
   parallel.
2. **Hard constraints are hard.** Mobility, medical, and pets/service animals
   filter shelters; they are never traded off against distance.
3. **No route through hazard.** A candidate route intersecting a current
   perimeter (plus buffer), a Level 3 zone, or a known closure is rejected. If
   nothing survives, say so — do not return the least-bad option as if safe.
4. **No re-entry without an explicit all-clear.** Absence of a zone from a
   public layer is not an all-clear. Hazmat or utility clearance unknown ⇒ no
   return recommendation.
5. **Stale ⇒ no downgrade.** A stale record cannot lower a level or clear a
   road.
6. **Coverage honesty.** If a source was denied, errored, or returned empty, the
   answer states which layer is missing and what that means.

The model may phrase these; it may not overturn them. The step trace labels each
step `model` or `safety guard`, as the previous MVP did.

## 8. The blocked-action demo

`app/sources/firecam.py` implements a real capability — pull the nearest fire
camera still for visual confirmation — against `cameras.alertwildfire.org`, which is
deliberately **not** in `policies/spokane-evac.yaml`. When the agent decides that
capability is relevant, it calls it, OpenShell returns 403, and the UI shows a
blocked action with the host and the policy reason.

This matters because the alternative demo — a fake tool that pretends to be
blocked — proves nothing. Here the enforcement is external to the agent, and the
same mechanism is what keeps the other six hosts to GET/HEAD.

## 9. Open decisions — all resolved

Each was a real fork. What the M0 probing actually found is in
[SOURCES.md](./SOURCES.md); the outcomes are summarised here.

| # | Decision | Outcome |
|---|---|---|
| 1 | SREC vs. Genasys for evacuation zones | **SREC.** Genasys serves an SPA at every `/api/**` route probed and was *removed* from the policy. SREC's org path was added. |
| 2 | Spokane county GIS host | **`gismo.spokanecounty.org`** is correct, but it carries no evacuation layer and the runtime does not call it, so the policy grant was removed. |
| 3 | Narrow the Genasys `/api/**` grant | Moot; the host is gone from the allowlist. |
| 4 | OSRM has no SLA and no closure knowledge | Confirmed. Closures are applied as a post-filter in `safety.validate_route`. A rejected route is an honest outcome. |
| 5 | Live vs. replay default | **Replay**, with a live toggle. See DEMO.md for why both are worth showing. |
| 6 | Weather/AQI | Out of scope. Not in the allowlist, not claimed in the UI. |

The original text of each follows.

1. **SREC vs. Genasys for evacuation zones.** The prior research identified
   SREC's own FeatureServer as the Tier-1 authority, at
   `services3.arcgis.com/9UdSzuxhN4jGcI9p/...`. The current policy allows only
   the `T4QMspbfLg3qTGWY` (NIFC) org on that host, so **SREC is currently
   blocked**, and evacuation levels would come from Genasys Protect instead. If
   SREC is wanted, add its org path to the policy. Recommendation: add SREC and
   treat Genasys as corroboration, since SREC is the local incident-command
   source.
2. **Spokane county GIS host.** Research cited `services.spokanegis.org`; the
   policy allows `gismo.spokanecounty.org`. Confirm which one serves the layers
   actually needed, and align the policy.
3. **Genasys route shape is undocumented**, hence the wholesale `/api/**` grant.
   Narrow it once the concrete routes are known.
4. **OSRM demo server has no SLA** and its graph will not know about today's
   closures. Closures must be applied as post-filters in `geo.py`; a rejected
   route is an honest outcome, not a routing bug.
5. **Live vs. replay default.** `.env.example` ships `replay`. Live is the point
   of this rewrite; replay is the safety net if the venue network or an upstream
   is down. Decide which the demo opens in.
6. **No weather/AQI in the policy.** NWS and AirNow were Tier-1/2 in the
   research but are absent from the allowlist. Either add them or state that
   weather context is out of scope for this build.

## 10. Non-goals

Turn-by-turn navigation. Any write to a government system. Real missing-person
filing. Authentication, roles, audit, retention, monitoring — all required for
production, none present here.
