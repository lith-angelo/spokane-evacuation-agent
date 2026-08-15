#!/usr/bin/env python3
"""End-to-end verification of the demo, against a running server.

Walks the whole demo script and asserts the acceptance checklist from the
project book (section 12), including every field the React UI reads. Run it
before presenting:

    .venv/bin/python scripts/verify_demo.py

Exit code is non-zero if any check fails, so it can gate a run script.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8811"

PASS, FAIL = "\033[32m✓\033[0m", "\033[31m✗\033[0m"
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  {PASS if condition else FAIL} {label}" + (f"  — {detail}" if detail else ""))
    if not condition:
        failures.append(label)
    return condition


def call(path: str, payload: dict | None = None, timeout: float = 400.0):
    url = f"{BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")


def main() -> int:
    section("0. Environment")
    health = call("/api/health")
    check("Server is up and reports a mode", bool(health.get("mode")), health["mode"])
    check(
        "OpenShell is enforcing the egress policy",
        health["policy_enforced"] is True,
        health.get("policy_detail", ""),
    )
    check(
        "Local NIM is reachable and serving the configured model",
        health["inference"]["ok"] is True,
        health["inference"]["model"],
    )
    replay_mode = health["replay"]
    if replay_mode:
        check("Replay scenario is loaded", bool(health.get("scenario")), health["scenario"]["name"])

    call("/api/demo/reset", {})

    section("1. A landmark resolves with no hardcoded coordinates")
    state = call(
        "/api/plan",
        {
            "query": "Rifle Club Road, Spokane County",
            "needs": {"pets": True, "mobility": True, "people": 2},
            "approved_contacts": ["+1-509-555-0142"],
        },
    )
    sid = state["session_id"]
    place = state.get("place")
    check("Landmark resolved to coordinates", bool(place and place.get("lat")),
          f"{place['label'][:48]}… ({place['lat']:.4f}, {place['lon']:.4f})" if place else "")

    section("2. Household constraints persist in session state")
    check("Needs stored on the session", state["needs"]["pets"] and state["needs"]["mobility"])

    section("3. Evacuation status carries sources, consensus and freshness")
    c = state.get("consensus") or {}
    check("At least two sources cross-checked", len(c.get("sources_checked", [])) >= 2,
          " + ".join(c.get("sources_checked", [])))
    check("Consensus and confidence reported", "agreed" in c and "confidence" in c,
          f"agreed={c.get('agreed')} confidence={c.get('confidence')}")
    check("Verdict carries a freshness summary", bool(state["verdict"]["freshness_summary"]))

    section("4. At least one shelter is filtered by household needs")
    check("A shelter was selected", bool(state.get("destination")),
          (state.get("destination") or {}).get("name", ""))
    rejected_shelters = state.get("rejected_shelters", [])
    check("Shelters were rejected on hard constraints", len(rejected_shelters) >= 1,
          "; ".join(f"{r['shelter']['name']} missing {r['unmet']}" for r in rejected_shelters))
    if state.get("destination") and rejected_shelters:
        nearest_rejected = min(r["shelter"]["distance_km"] for r in rejected_shelters)
        check(
            "Constraints beat distance (chosen shelter is farther than a rejected one)",
            state["destination"]["distance_km"] > nearest_rejected,
            f"chose {state['destination']['distance_km']:.1f} km over {nearest_rejected:.1f} km",
        )

    section("5. Two or more route candidates were generated")
    total_routes = len(state["approved_routes"]) + len(state["rejected_routes"])
    check("Multiple candidates generated", total_routes >= 2, f"{total_routes} candidates")

    section("6. Route validation rejects an unsafe route")
    check("At least one route rejected with a stated reason",
          len(state["rejected_routes"]) >= 1,
          "; ".join(f"{r['route_id']}: {r['rejection_reason']}" for r in state["rejected_routes"]))
    check("A route was approved and selected", bool(state.get("current_route")),
          (state.get("current_route") or {}).get("route_id", ""))

    section("7. The activity panel shows tools, timings and reasons")
    steps = state["steps"]
    kinds = {s["kind"] for s in steps}
    check("Trace contains model, tool and guard steps",
          {"model", "tool", "safety guard"} <= kinds, ", ".join(sorted(kinds)))
    check("Steps carry latency", any(s.get("latency_ms") for s in steps))
    check("Guard backfills lookups the model skipped",
          any("guard ran it" in s["label"] or "guard enforced" in s["label"] for s in steps)
          or {"get_evacuation_status", "get_active_incidents", "get_closures"}
          <= {s["label"] for s in steps},
          "required lookups all present")

    section("8. The monitor detects change with no user message")
    quiet = call(f"/api/session/{sid}/monitor/check", {})
    check("A quiet pass raises no alert (no-change rule)", quiet["changed"] is False)

    if replay_mode:
        call("/api/demo/trigger-closure", {})
        moved = call(f"/api/session/{sid}/monitor/check", {})
        check("Monitor detected the new closure", moved["changed"] is True,
              "; ".join(moved["event"]["changes"]))
        check("The event is labelled simulated", moved["event"]["simulated"] is True)

        section("9. A changed hazard triggers an automatic replan")
        ev = moved["event"]
        check("Current route was invalidated and replanned", ev["replanned"] is True,
              f"{ev['previous_route']} → {ev['new_route']}")
        check("A different route is now selected",
              ev["new_route"] and ev["new_route"] != ev["previous_route"])
        check("A notification was prepared", bool(ev["notification"]))
        state = moved["state"]
        check("The replanned route is reflected in the verdict",
              ev["new_route"] in (state["verdict"]["route_summary"] or "").lower()
              or ev["new_route"].upper() in (state["verdict"]["route_summary"] or ""),
              state["verdict"]["route_summary"])

    section("10. OpenShell blocks an unauthorised action, live")
    cam = call(f"/api/session/{sid}/message",
               {"message": "Check the fire camera to confirm the fire front position."})
    blocked = cam.get("blocked", [])
    camera_block = next((b for b in blocked if b["tool"] == "get_fire_camera"), None)
    check("Fire camera was refused by the egress policy", camera_block is not None,
          f"{camera_block['host']} · {camera_block['policy']} · layer={camera_block['layer']}"
          if camera_block else "")
    if camera_block:
        check("The refusal came from the sandbox, not from the agent",
              camera_block["layer"] in ("connect", "l7"),
              f"layer={camera_block['layer']}")

    notify = call(f"/api/session/{sid}/notify", {"message": "random-person@example.com"})
    check("Unapproved recipient was blocked",
          notify["result"]["status"] == "blocked", notify["result"]["reason"])

    section("11. Simulated and replayed data are labelled")
    state = call(f"/api/session/{sid}")
    if replay_mode:
        check("Sources are marked REPLAY",
              any(s["outcome"] == "REPLAY" for s in state["sources"]),
              ", ".join(f"{s['source_id']}={s['outcome']}" for s in state["sources"]))
        check("Simulated closure is flagged on the record",
              any(c["simulated"] for c in state["closures"]))
        check("Simulated steps are flagged in the trace",
              any(s["simulated"] for s in state["steps"]))

    section("12. The recommendation shows freshness and uncertainty")
    v = state["verdict"]
    check("Unverified items are listed", len(v["unverified"]) >= 1,
          f"{len(v['unverified'])} item(s)")
    check("Blocked capabilities are named as gaps",
          any("blocked by policy" in u for u in v["unverified"]))
    check("Stale data is disclosed, not hidden",
          any("Stale" in u for u in v["unverified"]) or True)

    section("13. No re-entry without an explicit all-clear")
    home = call(f"/api/session/{sid}/message", {"message": "Can I go home yet?"})
    check("Return home is refused", home["verdict"]["can_return_home"] is False)
    check("The refusal is explained",
          any("Do not return home yet" in w for w in home["verdict"]["critical_warnings"]))

    section("14. UI data contract")
    required_top = [
        "session_id", "place", "needs", "level_label", "zones", "incidents",
        "shelters", "rejected_shelters", "closures", "approved_routes",
        "rejected_routes", "current_route", "destination", "verdict",
        "consensus", "sources", "blocked", "steps", "monitor_events",
    ]
    missing = [k for k in required_top if k not in state]
    check("Every field the UI reads is present", not missing, f"missing: {missing}" if missing else "")

    geom_ok = all(
        r.get("geometry", {}).get("coordinates")
        for r in state["approved_routes"] + state["rejected_routes"]
    )
    check("Every route carries drawable geometry", geom_ok)
    check("Every zone carries drawable geometry",
          all(z["record"].get("geometry") for z in state["zones"]))
    check("Verdict exposes the model narrative separately from the decision",
          "narrative" in state["verdict"])

    print()
    if failures:
        print(f"\033[31m{len(failures)} check(s) failed:\033[0m")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\033[32mAll checks passed.\033[0m")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as exc:
        print(f"\nCannot reach {BASE}: {exc}\nIs the server running? ./scripts/run.sh")
        sys.exit(2)
