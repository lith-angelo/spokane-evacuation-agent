# Demo script

Three to five minutes, following the structure in the project book (section 9).
Two modes are worth showing and they tell different stories — read
[Which mode to demo](#which-mode-to-demo) before you decide.

## Before you start

```bash
.venv/bin/python scripts/verify_demo.py     # walks the whole script, exits non-zero on any failure
./scripts/run.sh                            # replay mode, http://127.0.0.1:8811
```

`verify_demo.py` checks all twelve items on the book's acceptance checklist plus
the fields the UI reads. If it passes, the demo will run.

Check the three chips in the header before you speak:

- `● REPLAY` or `● LIVE` — which data the agent is reading
- `OpenShell enforcing` — the containment moment will be real
- `Qwen3.6-35B-A3B-NVFP4` — the local NIM is answering

If `OpenShell enforcing` is red, the policy is not applied and the best moment in
the demo will not land:

```bash
nemoclaw my-assistant policy-add --from-file ./policies/spokane-evac.yaml --yes
```

---

## 0:00–0:30 — The problem

Emergency information is fragmented and sometimes contradicts itself. Ordinary
navigation optimises for travel time and knows nothing about fire. Nobody's
evacuation plan accounts for two dogs and a walker.

Leave the app on screen with the preset loaded and nothing run yet.

## 0:30–1:05 — The resident

The preset is already filled in: **Rifle Club Road, Spokane County**, with
**Pets** and **Mobility** selected.

Press **Plan my evacuation** and talk over the activity panel while it fills.

> Every line in that panel is a real tool call — its arguments, its result, how
> long it took. No hidden reasoning, just what the agent actually did.

Point out in the trace:

- `get_evacuation_status` — **2 sources checked, consensus, confidence**. The
  cross-check is between SREC's declared evacuation level and NIFC's fire
  geometry. Two independent authorities, not two views of one table.
- `get_active_incidents` — the Rifle Club Fire, its distance, and **when it was
  last observed**.

## 1:05–1:45 — A personalised, validated plan

The recommendation card is now filled in. The three things to point at:

**1. The nearest shelter is not the answer.** Spokane Falls Community College is
4.1 km away and is rejected — its notes say *"NO PETS — service animals only"*.
Nine Mile Falls School is 7.6 km away and rejected — *"NOT ADA accessible"*. The
agent sends them 17.8 km to the Fair & Expo Center because it is the only site
that meets both requirements.

> Hard constraints are filters, not preferences. They are applied before
> anything is ranked by distance.

**2. Routes are validated, not just generated.** Three candidates came back from
the router. Route C is rejected — it runs through a closure on W Driscoll Blvd.
Route A is selected. Show the map: the grey dashed line is the rejected route.

> A router returning a path is not a safety claim. The validator rejects
> anything that touches a closure, a fire perimeter plus a 1.5 km buffer, or
> spends most of its length inside a Level 3 zone.

**3. The verdict is not written by the model.** Point at the panel header —
*"decided by the safety guard, not the model"* — and at the model's prose
underneath, which is explicitly labelled as rendered *around* the decision.

## 1:45–2:30 — The always-on moment

This is the part that makes it an agent.

Press **▶ Start monitor**, then **⚡ Simulate road closure**.

**Say nothing to the agent. Ask it nothing.** Then watch the activity panel:

```
--- simulated event ---
MONITOR   Road-state change detected     new hard closure: W Francis Ave at N Assembly St
GUARD     Current route route-A invalidated   simulated closure: W Francis Ave
MONITOR   Replanning
GUARD     New route route-B approved     24.1 km, 30 min
```

The map redraws: route A turns grey, route B turns blue.

> The trigger publishes a new closure at the *source*. It does not tell the
> agent anything. The monitor fetches on its own interval, compares against what
> it already believed, works out that its own route is dead, replans, revalidates
> and prepares a notification — with no user message anywhere in that sequence.
>
> The pink line and the `[SIM]` markers are there because replayed and simulated
> events have to be labelled. That rule is in the design.

## 2:30–3:05 — The containment moment

Two blocks, and neither is simulated by the agent.

**First — a network capability.** Press **Ask the agent to use the fire camera**.

The agent decides a camera would help, builds a real request to
`cameras.alertwildfire.org`, and OpenShell's L7 proxy refuses the CONNECT. The panel
shows the host, the policy name, and the layer the refusal came from.

Then prove the boundary is outside the agent:

```bash
nemoclaw my-assistant policy-list               # spokane-evac is applied
grep -n alertwildfire policies/spokane-evac.yaml
```

> The only mention of that host in the policy file is a comment explaining why
> it is *absent*. The agent cannot edit this file.

If someone pushes on whether the block is real, this is the answer — and it has
been run:

> `cameras.alertwildfire.org` is the camera network fire agencies actually use.
> It resolves, and it answers. Add it to the policy, re-apply, and the tool
> returns **HTTP 200**. Take it out, re-apply, and it is refused again. The
> policy is the only thing standing between the agent and that data.

Verified end to end while building this — policy versions 12 → 13 → 14, denied →
`HTTP 200` → denied. The sequence is recorded in
[SOURCES.md](./SOURCES.md#deliberately-denied--camerasalertwildfireorg).

An earlier revision of this tool pointed at a placeholder domain, and that was
worth fixing: a host that does not exist fails whether or not a policy exists, so
the refusal proved nothing. Do not let anyone accept the weaker version.

**Second — an action scope.** Press **Send the plan to an unapproved recipient**.

```
Action blocked
Reason: recipient random-person@example.com was not approved in the current session
```

> Network scope and action scope are separate policies. The first is enforced by
> the proxy, the second by the session. Both refuse rather than negotiate.

## 3:05–3:35 — The NVIDIA story

- **Nemotron Lightning via NIM on the GN100** — `nvidia/Qwen3.6-35B-A3B-NVFP4`,
  served locally. The header chip is live; nothing leaves the box for inference.
- **NemoClaw** orchestrates the agent loop and owns the sandbox.
- **OpenShell** enforces the egress policy the demo just ran into.
- The whole trace is local: `curl localhost:8811/api/health` shows the sandbox,
  the policy, the model and the snapshot count in one object.

## 3:35–4:00 — Close

> This does not answer wildfire questions. It keeps an evacuation plan current
> while the environment changes underneath it — and it is honest about the
> difference between "we checked and it is clear" and "we could not check".

---

## Which mode to demo

**Replay** (`./scripts/run.sh`) is the default and the safe choice. The scenario
is the Rifle Club Fire, northwest of Spokane. Roads, drive times, route geometry,
highway alerts and the 113-site emergency facility list are **real, captured
live**. The evacuation overlay — zones, activated shelters, local closures — is
**authored**, because SREC publishes nothing when no evacuation is running. On
the day the fixtures were built that layer returned a literal `{"count":0}`.

**Live** (`./scripts/run.sh --live`) fetches everything at request time through
the OpenShell policy, and on a quiet day it tells a different but arguably
stronger story:

```
LEVEL    : Level 2 — SET
SOURCES  : SREC OK (110 records) · WFIGS OK (11) · WSDOT OK (2) · WSDOT_EOC UPSTREAM_ERROR
WARNING  : This address is inside the mapped perimeter of OLD TRAILS
           (WFIGS, as of 2026-08-06), but no evacuation zone is published for
           this location. Absence of a zone is not an all-clear.
WARNING  : No shelter meets every hard requirement for this household.
           Fire Station 17 lacks pets, mobility. Call 211 or 911.
```

That is real, current NIFC data. A real 2026 fire perimeter covers that address,
the county has published no evacuation zone for it, and the agent refuses to
call it clear — it escalates to Level 2, says exactly why, and declines to
invent a shelter or a route it cannot justify.

If you have time for one extra minute, show both. Replay demonstrates the happy
path end to end; live demonstrates that the honesty is not a story the fixtures
tell.

**Note on `WSDOT_EOC`.** That feed genuinely returns
`Unable to perform query operation` — the layer is published but its query
endpoint is broken upstream. It is left in deliberately. A source that is down
shows up as `UPSTREAM_ERROR` and is reported as a coverage gap, never as an
absence of hazard.

---

## If something goes wrong

| Symptom | Fix |
|---|---|
| Header shows `OpenShell NOT enforcing` | `nemoclaw my-assistant policy-add --from-file ./policies/spokane-evac.yaml --yes` |
| Model chip is red | Check the NIM: `curl localhost:8000/v1/models` |
| Venue network is down | You are already fine in replay — only the map tiles need the network, and the geometry still draws without them |
| Trigger says "only exists in replay mode" | You are in live mode; the monitor is watching real feeds there |
| Need to run the whole thing again | `curl -X POST localhost:8811/api/demo/reset` then reload the page |

## Questions you will be asked

**"Is the block real, or did you code it to fail?"**
`app/sources/firecam.py` has no failure branch. It builds a request and calls
the same egress path every other source uses. Add the host to the policy,
re-apply, and the tool starts working. That is the only way to make it work.

**"What if the model hallucinates an all-clear?"**
It cannot set a decision. `Verdict` is produced by `app/safety.py` from the
records the tools returned; the model's text is a separate field rendered around
it. A hallucinating model changes the wording on the page and none of its
decisions. `tests/test_safety.py` has a negative control for each gate, and each
one has been verified to fail when its gate is removed.

**"How much of this is the LLM?"**
Intent parsing, tool selection, and the prose. Point-in-polygon, level
comparison, constraint filtering, staleness, route validation and the re-entry
decision are all deterministic code with no model in the path.

**"Is this ready for real use?"**
No, and it says so on every screen. It is a prototype: no authentication, no
audit, no retention, no on-call, and no validation by any public-safety agency.
The routing is not certified navigation and the agent is not 911.
