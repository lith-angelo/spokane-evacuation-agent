# Spark Hack Seattle submission package

Deadline: Sunday, August 16, 2026 at 11:00 AM PDT.

Submission form:
https://airtable.com/appCdxYXDYp7snYbP/pagqXe6ElIlXx6oa3/form

## Proposed submission fields

- **Team name:** EMBER
- **Challenge:** Do - AI that plans, acts, and gets things done
- **Bounty:** Best Use of NVIDIA Nemotron
- **Repository:**
  https://github.com/linyueduan5-code/spokane-evacuation-agent
- **Demo access (event Tailscale only):** `http://gn100-2b2a:8811`
- **Team roster:** REQUIRED - add names, roles, and contacts before submission.
- **Video URL:** REQUIRED - add the unlisted YouTube or Vimeo URL.

## Project description

EMBER is an always-on wildfire evacuation agent for Spokane County. A resident
enters a location and non-negotiable household needs such as pets, wheelchair
access or medical support. NVIDIA Nemotron 3.5 Lightning, running locally on the
GN100, interprets the request and orchestrates seven data tools. A deterministic
safety guard then cross-checks evacuation zones and wildfire geometry, rejects
shelters that violate hard constraints, validates multiple routes against known
closures and fire perimeters, and owns the final recommendation.

The work continues after the first answer. When a new road closure appears, the
monitor detects the material state change without another user message,
invalidates the affected route, replans, and prepares a clearly labelled
notification. NemoClaw provides the agent runtime and OpenShell enforces a
deny-by-default network/filesystem boundary. The demo includes real allow and
deny probes, source provenance, freshness, uncertainty, and explicit separation
of live, replayed, derived and synthetic data.

## Why the GN100 and Nemotron matter

- Lightning performs real intent parsing, tool selection, structured extraction,
  state comparison, narrative generation and replanning decisions.
- Local inference keeps a safety-sensitive location workflow off a hosted LLM,
  reduces network dependence and makes the full agent observable on one device.
- The GN100 runs the model, sandbox, FastAPI harness and UI together while the
  deterministic guard prevents model text from becoming an evacuation order.

## Prior-work disclosure

A separate public concept branch, `codex/spokane-evacuation-mvp`, predates the
event. It was not merged into this submission, and the current implementation
shares no Git blobs with it. The submitted branch's pre-event initialization
commit is empty; its runnable commits begin during the hackathon. Keep this
history visible and ask the organizer whether they want the concept branch
called out in the form or video.

## Four-minute video run-of-show

Keep the camera on and minimize cuts.

1. **0:00-0:25 - Team.** Say the team name and each member's role.
2. **0:25-0:55 - Hook.** "EMBER does not merely answer a wildfire question. It
   checks public evidence, respects pets and mobility needs, rejects unsafe
   options, then keeps watching and replans when the world changes."
3. **0:55-2:05 - Live core loop.** Open the Tailscale demo, use the Rifle Club
   scenario, plan the evacuation, point out the selected shelter, three route
   candidates and the rejected Driscoll route. Start the monitor, simulate the
   Francis closure, and show route A change to route B without a new message.
4. **2:05-3:30 - Engineering depth.** Show the activity trace and architecture.
   Explain Lightning's tool/state role, the deterministic safety guard,
   replay/live labels, and the OpenShell refusal of the camera tool. Show the
   health badges proving the exact model and enforced policy.
5. **3:30-4:00 - So what.** Explain why a local, observable, fail-closed agent is
   more trustworthy during a network-constrained emergency and name the next
   production steps.

## Official checklist

| Required item | State | Evidence / action |
|---|---|---|
| One submission per team | Confirm | Team owner must submit once. |
| Team name | Ready | EMBER (confirm spelling). |
| Project description | Ready | Text above. |
| Challenge selected | Ready | Do. |
| 3-5 minute live demo video | Missing | Record with camera on; upload unlisted to YouTube/Vimeo. |
| Public repo or judge access | In progress | The repository is public, but the latest reviewed worktree must still be committed and pushed. |
| README quick start | Ready | `README.md` Quick start. |
| Tech stack and architecture | Ready | `README.md` Architecture. |
| Reproduction env/API keys | Ready | `.env.example`, no real keys committed. |
| Dataset/synthetic provenance | Ready | `README.md`, `docs/SOURCES.md`, fixture metadata. |
| Known limitations and next steps | Ready | `README.md` final sections. |
| Deployed URL or screen capture | Ready | Tailscale URL above; video will capture it. |
| Team roster: names, roles, contacts | Missing | Obtain from the team; do not invent or publish without consent. |
| Prior-concept organizer confirmation | Missing | Disclose the separate pre-event concept branch and ask whether any additional note is required. |

## Final pre-submit commands

```bash
curl http://gn100-2b2a:8811/api/health
.venv/bin/python scripts/verify_demo.py http://gn100-2b2a:8811
```

Require `policy_enforced=true`, the exact Lightning model ID and `All checks
passed` before recording.
