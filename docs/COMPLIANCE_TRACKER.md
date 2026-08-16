# Spark Hack Seattle Compliance Tracker

Last updated: 2026-08-15

Status values: `PASS`, `IN PROGRESS`, `BLOCKED`, `NOT VERIFIED`.

| ID | Requirement or risk | Status | Current evidence | Exit criteria |
|---|---|---|---|---|
| C01 | Run the working system locally on the GN100 | PASS | The contained FastAPI runtime is reachable through an OpenShell service on the GN100; the local vLLM endpoint runs on port 8000. | Preserve in final verification. |
| C02 | Nemotron Lightning is a meaningful core model | PASS | `NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` is served locally; recorded traces show it selecting evacuation, shelter, route-planning, and route-validation tools. | Preserve trace evidence and explain its role in the submission. |
| C03 | Use open data and identify provenance/freshness | PASS | SREC, WFIGS, WSDOT, Spokane County GIS, and per-record timestamps are represented. | Recheck final UI labels and source links. |
| C04 | Clearly distinguish replay, synthetic, derived, and live data | PASS | The UI displays `REPLAY`; simulated closure events and replay records are labelled. | Preserve labels in screenshots and video. |
| C05 | OpenShell policy is actually enforcing | PASS | Host-direct fallback is disabled; health reports `policy_enforced=true`; OpenShell denied ALERTWildfire while approved Mapbox geocoding succeeded. | Preserve the allow-and-deny probes in final verification. |
| C06 | Use both NemoClaw and OpenShell to contain the capable agent | PASS | The complete FastAPI harness runs inside the `my-assistant` NemoClaw sandbox with `EVAC_INSIDE_OPENSHELL=1`; its full verifier passed through the exposed OpenShell service. | Preserve the contained launch and verification evidence in the final demo. |
| C07 | Enforce least-privilege network and filesystem policy | PASS | Runtime preset version 14 grants five required public-data hosts to Python/curl, plus local inference. Unused GIS/weather/training hosts and Node were removed; built-in OpenClaw endpoints cannot be borrowed by curl; `/etc` writes and host-repository access were denied. | Re-run the negative probes after any policy or sandbox-image change. |
| C08 | Active OpenShell policy matches the repository policy | PASS | Dry-run succeeded; repository policy version 14 was applied and loaded with hash `85bf4ccaa69c`; removed-host and allowed-host probes behaved as declared. | Re-export after any policy edit and preserve the final hash. |
| C09 | NemoClaw inference configuration uses Lightning | PASS | NemoClaw reports `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4`; `inference.local` is reachable and the host vLLM backend is healthy. | Preserve this status in final verification. |
| C10 | Unsafe route is rejected with an explicit reason | PASS | Full verifier generated three candidates and rejected route C for the W Driscoll Blvd closure. | Preserve in final verification. |
| C11 | Closure triggers autonomous replan and notification preparation | PASS | Monitor detected the Francis Ave closure, changed route A to route B, and prepared a labelled simulated notification. | Preserve in final verification. |
| C12 | Backend, frontend, and full demo validation pass | PASS | Backend 72/72, frontend 20/20, production build, and the complete `verify_demo.py` flow pass through the contained public port. | Repeat after final changes. |
| C13 | No credentials are committed or exposed publicly | IN PROGRESS | Current tree, untracked files, and every Git revision scan clean. Sensitive URL query values are redacted before logging/persistence. The previously shared Mapbox public token and unused DeepSeek key still require account-side rotation. | Rotate both shared credentials, update only the sandbox `.env`, and repeat the live Mapbox probe. |
| C14 | Demo personal data is synthetic and privacy limitations are clear | PASS | UI has an always-visible synthetic-data/no-delivery/not-911 notice; replay startup purges sessions, steps and snapshots; health exposes retention; public port binds only to the GN100 Tailscale address. | Preserve the Tailscale-only bind and restart-purge behavior. |
| C15 | Public GitHub repository contains the final system | PASS | Reviewed runtime scope was committed as `cf00ff0`, pushed to public branch `codex/gn100-contained-demo`, and opened as draft PR #2; an unauthenticated raw README request returned HTTP 200. Experimental unused training artifacts were intentionally excluded. | Merge PR #2 after teammate review; preserve the public branch until submission. |
| C16 | Required code was written during the event | PASS | The pre-event initialization commit contains no files; functional commits begin during the hackathon. | Preserve commit history and do not squash it into a misleading pre-event timestamp. |
| C17 | Third-party code complies with the two-week open-source rule | PASS | Python and Node direct dependencies are exact-pinned; complete lockfiles exist; `docs/THIRD_PARTY_NOTICES.md` records versions, licenses, upstreams, and public-since dates years before the 2026-08-01 cutoff. | Preserve lockfiles and notices; do not add an unreviewed dependency before code freeze. |
| C18 | Team, onsite GN100 rule, track-specific Notion rules, and video criteria | BLOCKED | Public Notion, video instructions, submission checklist and Nemotron bounty criteria were read. Do-track and technical criteria pass; GN100 remains onsite. Actual names/roles/contacts and one-submission ownership require team input. | Team provides and consents to roster/contact publication, and identifies the single submitter. |
| C19 | Submission includes repository, description, and 3-5 minute video | IN PROGRESS | Final code is public in PR #2; `docs/SUBMISSION.md` contains the description, Do track, checklist, and four-minute script. Video URL and form submission are not verified. | Merge the reviewed PR, record/upload the live video with camera on, add its URL, and submit before 11:00 AM PDT. |
| C20 | Documentation describes the real security boundary | PASS | README distinguishes the submitted contained runtime from host development mode, and the observed deployment now matches the contained-runtime description. | Recheck after any deployment or architecture change. |

## Verification log

### 2026-08-15 - Initial strict audit

- Confirmed local Nemotron Lightning through `/v1/models` and a running vLLM process.
- Confirmed meaningful model tool selection from a persisted agent trace.
- Confirmed backend tests: 68 passed.
- Confirmed frontend tests: 20 passed.
- Confirmed frontend production build succeeds.
- Confirmed tracked secret-pattern scan returned zero matches.
- Confirmed the pre-event initialization commit is empty.
- Observed `policy_enforced=false` and host-direct fallback enabled.
- Observed sandbox stuck in `Provisioning`, with Qwen configured and inference unhealthy.
- Observed the applied sandbox policy does not include Mapbox.
- Observed full demo verification failures in route rejection and automatic replanning.

### 2026-08-15 - NemoClaw recovery and inference alignment

- Rebuilt `my-assistant`; OpenShell now reports the sandbox phase as `Ready`.
- Set the NemoClaw `vllm-local` inference route to the exact Lightning model.
- Verified inference through `inference.local` and the host vLLM backend.
- C09 moved to `PASS`; containment and least-privilege items remain open.

### 2026-08-15 - Enforced egress restored

- Applied and loaded `spokane-evac` policy version 5 after a successful dry run.
- Replaced the nonexistent NemoClaw runtime path with the installed CLI path.
- Disabled `ALLOW_HOST_DIRECT_FALLBACK` and restarted the FastAPI service.
- Verified a real OpenShell denial for the omitted ALERTWildfire host.
- Verified an approved Mapbox geocoding request succeeds with enforcement active.
- C05 and C08 moved to `PASS`; C06 and C07 remain open.

### 2026-08-15 - Policy narrowing and route/replan repair

- Removed the active Brew, Hugging Face, npm, PyPI, and OpenRouter/pricing presets.
- Restricted Mapbox policy access to temporary geocoding; routing now uses the
  OpenShell-compatible OSRM encoded-polyline endpoint.
- Added scenario-aware behavior: Rifle Club uses its labelled captured routes;
  unrelated addresses are not snapped to the authored origin and may use live
  Mapbox geocoding plus live OSRM routing.
- Added regression tests for replay query selection and live OSRM bypass.
- Backend suite now reports 70 passing tests.
- Full verifier completed with exit code 0 and every check passed.
- C10, C11, and C12 moved to `PASS`; C07 remains `IN PROGRESS` pending the
  filesystem and built-in harness policy review.

### 2026-08-15 - Full harness containment

- Uploaded the application into the ready `my-assistant` NemoClaw sandbox and
  started FastAPI with `EVAC_INSIDE_OPENSHELL=1`.
- Installed dependencies only during sandbox setup, then removed the temporary
  PyPI policy before running the application.
- Ran all 70 backend tests inside the sandbox.
- Ran the complete `scripts/verify_demo.py` flow through the exposed OpenShell
  service; every check passed, including the live policy denial.
- Confirmed the contained service serves the production web application with
  HTTP 200.
- C06 and C20 moved to `PASS`; C07 remains `IN PROGRESS` until the final
  filesystem and built-in endpoint review is complete.

### 2026-08-15 - Least-privilege runtime policy

- Removed unused `gismo.spokanecounty.org`, `api.weather.gov`, and
  `archive-api.open-meteo.com` grants from the seven-tool runtime policy.
- Removed the unused Node binary grant; retained Python for the FastAPI agent
  and local inference client, and curl for policy-governed public-data calls.
- Applied and loaded OpenShell policy version 14 (hash `85bf4ccaa69c`).
- Confirmed Nominatim remains allowed and the removed NWS host is denied.
- Confirmed built-in ClawHub access is scoped to the OpenClaw binary and cannot
  be borrowed by the agent's curl process.
- Confirmed `/etc` is read-only, the host repository is not mounted, and only
  sandbox-local mutable state is visible to the contained process.
- Re-ran the complete verifier through the public GN100 port after switching it
  to the OpenShell forward; all checks passed. C07 moved to `PASS`.

### 2026-08-15 - Credential and demo-data hardening

- Scanned tracked files, untracked files, and every Git revision for DeepSeek
  and Mapbox token patterns; no credential was found.
- Added query-secret redaction before an egress URL can enter state, logs, or
  persistence, with a regression test.
- Added an always-visible synthetic-data/no-delivery/not-911 notice.
- Replay mode now purges sessions, steps, and snapshots whenever FastAPI starts;
  the deployed database was confirmed empty after restart.
- Rebound the public demo forward from all interfaces to the GN100 Tailscale
  address `100.84.72.29:8811` and verified it from the presenter Mac.
- Backend suite now reports 72 passing tests; frontend remains 20/20 and the
  production build passes. C14 moved to `PASS`; C13 still needs account-side
  rotation of the two credentials previously pasted into chat.

### 2026-08-15 - Public branch and dependency audit

- Reviewed the mixed worktree and excluded the unused experimental fire-growth
  training files from the submission scope.
- Committed the contained runtime as `cf00ff0`, pushed public branch
  `codex/gn100-contained-demo`, and opened draft PR #2 against `main`.
- Verified the branch without GitHub authentication using a raw HTTP request.
- Exact-pinned all direct Python and Node dependencies, captured the complete
  tested Python environment, and retained npm integrity hashes in its lockfile.
- Added license, upstream, and public-since evidence for every direct dependency;
  all upstream projects predate the event cutoff by years.
- `pip check`, lock/environment diff, 72 backend tests, clean npm install,
  20 frontend tests, npm audit, and production build all passed.
- C15 and C17 moved to `PASS`; C19 remains open for teammate review/merge and
  the required video/submission-form steps.

### 2026-08-15 - Official Notion and submission audit

- Decoded the official Notion URL from the Deck QR code and read the public
  event page, linked submission checklist, demo-video instructions, and
  Nemotron bounty criteria.
- Confirmed the project fits the `Do` track and the bounty requires Nemotron to
  be central, reliable, grounded, useful and differentiated; the implemented
  tool/state/replan role and evaluator cover those technical criteria.
- Confirmed the video must be 3-5 minutes, keep the camera on, show the live core
  loop, explain engineering depth/hardware choices, and close with impact.
- Added `docs/SUBMISSION.md` with ready submission copy, exact checklist and a
  four-minute run-of-show; added the Tailscale demo URL and next steps to README.
- Re-ran the complete 14-stage verifier against the Tailscale-bound,
  OpenShell-forwarded release candidate after all code hardening; exit code 0,
  all checks passed.
- C18 is now `BLOCKED` only on real roster/contact/single-submitter facts that
  cannot be inferred safely. C19 remains open only for merge, video and form.

## Change rule

Move an item to `PASS` only after its exit criterion is demonstrated by a live
command, test, exported policy, or submission artifact. Configuration intent or
a UI badge alone is not evidence.
