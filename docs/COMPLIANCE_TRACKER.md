# Spark Hack Seattle Compliance Tracker

Last updated: 2026-08-15

Status values: `PASS`, `IN PROGRESS`, `BLOCKED`, `NOT VERIFIED`.

| ID | Requirement or risk | Status | Current evidence | Exit criteria |
|---|---|---|---|---|
| C01 | Run the working system locally on the GN100 | PASS | The contained FastAPI runtime is reachable through an OpenShell service on the GN100; the local vLLM endpoint runs on port 8000. | Preserve in final verification. |
| C02 | Nemotron Lightning is a meaningful core model | PASS | `NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` is served locally; recorded traces show it selecting evacuation, shelter, route-planning, and route-validation tools. | Preserve trace evidence and explain its role in the submission. |
| C03 | Use open data and identify provenance/freshness | PASS | SREC, WFIGS, WSDOT, NASA FIRMS, OpenAQ, and per-record timestamps are represented. | Recheck final UI labels and source links. |
| C04 | Clearly distinguish replay, synthetic, derived, and live data | PASS | The UI displays the active `LIVE` or `REPLAY` mode; simulated closures, replay records, and derived values are separately labelled. | Preserve labels in screenshots and video. |
| C05 | OpenShell policy is actually enforcing | PASS | Host-direct fallback is disabled; health reports `policy_enforced=true`; OpenShell denied ALERTWildfire while approved Mapbox geocoding succeeded. | Preserve the allow-and-deny probes in final verification. |
| C06 | Use both NemoClaw and OpenShell to contain the capable agent | PASS | The complete FastAPI harness runs inside the `my-assistant` NemoClaw sandbox with `EVAC_INSIDE_OPENSHELL=1`; its full verifier passed through the exposed OpenShell service. | Preserve the contained launch and verification evidence in the final demo. |
| C07 | Enforce least-privilege network and filesystem policy | PASS | Runtime preset version 15 grants seven required public-data hosts to Python/curl, plus local inference. Unused GIS/weather/training hosts and Node were removed; built-in OpenClaw endpoints cannot be borrowed by curl; `/etc` writes and host-repository access were denied. | Re-run the negative probes after any policy or sandbox-image change. |
| C08 | Active OpenShell policy matches the repository policy | PASS | Repository policy revision 15 is loaded with hash `a3e683a817a7`; approved-source probes succeed and ALERTWildfire remains denied. | Re-export after any policy edit and preserve the final hash. |
| C09 | NemoClaw inference configuration uses Lightning | PASS | NemoClaw reports `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4`; `inference.local` is reachable and the host vLLM backend is healthy. | Preserve this status in final verification. |
| C10 | Unsafe route is rejected with an explicit reason | PASS | Full verifier generated three candidates and rejected route C for the W Driscoll Blvd closure. | Preserve in final verification. |
| C11 | Closure triggers autonomous replan and notification preparation | PASS | Monitor detected the Francis Ave closure, changed route A to route B, and prepared a labelled simulated notification. | Preserve in final verification. |
| C12 | Backend, frontend, and full demo validation pass | PASS | Backend 111/111, frontend 25/25, production build, and the complete `verify_demo.py` flow pass through the contained service. | Repeat after final changes. |
| C13 | No credentials are committed or exposed publicly | IN PROGRESS | Current tree, untracked files, and every Git revision scan clean. Sensitive URL query values are redacted before logging/persistence. A dedicated Mapbox token was created, installed only in host/sandbox `.env` files, verified through live autocomplete, and the previously exposed default Mapbox token was refreshed. The unused DeepSeek key still requires account-side revocation. | Revoke the previously shared DeepSeek key and confirm that no active deployment depends on it. |
| C14 | Demo personal data is synthetic and privacy limitations are clear | PASS | UI has an always-visible synthetic-data/no-delivery/not-911 notice; replay startup purges sessions, steps and snapshots; health exposes retention; public port binds only to the GN100 Tailscale address. | Preserve the Tailscale-only bind and restart-purge behavior. |
| C15 | Public GitHub repository contains the final system | IN PROGRESS | The repository and draft PR #2 are public, but the latest live-data, shelter-safety, UI, dependency, and documentation changes remain in the GN100 worktree. Experimental training artifacts are ignored and excluded. | Review, commit, push, and confirm the final branch is the repository URL submitted to judges. |
| C16 | Required code was written during the event | IN PROGRESS | The current main ancestry starts with an empty pre-event initialization commit and functional commits begin during the hackathon. A separate pre-event concept branch exists; it was not merged, shares no Git blobs with this implementation, and is disclosed in `docs/DESIGN.md`. | Preserve history and ask the organizer whether the disclosed prior concept requires any additional note in the submission. |
| C17 | Third-party code complies with the two-week open-source rule | PASS | Direct dependencies are exact-pinned to releases uploaded no later than 2026-07-31; complete integrity lockfiles exist; `docs/THIRD_PARTY_NOTICES.md` records exact-version registry dates; npm audit reports zero vulnerabilities. | Preserve lockfiles and notices; do not add an unreviewed dependency before code freeze. |
| C18 | Team, onsite GN100 rule, track-specific Notion rules, and video criteria | BLOCKED | Public Notion, video instructions, submission checklist and Nemotron bounty criteria were read. Do-track and technical criteria pass; GN100 remains onsite. Actual names/roles/contacts and one-submission ownership require team input. | Team provides and consents to roster/contact publication, and identifies the single submitter. |
| C19 | Submission includes repository, description, and 3-5 minute video | IN PROGRESS | `docs/SUBMISSION.md` contains the description, Do track, checklist, and four-minute script. Latest code is not pushed; the video URL and form submission are not verified. | Push the reviewed final branch, record/upload the live video with camera on, add its URL, and submit before 11:00 AM PDT. |
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

### 2026-08-15 - Mapbox credential rotation

- Created a dedicated `EMBER GN100 geocoding` Mapbox public token without URL
  restrictions because the backend geocoder does not send a browser origin.
- Installed the token only in the GN100 host and contained sandbox `.env` files;
  no token value was printed, logged, or added to Git.
- Restarted the contained FastAPI service and confirmed OpenShell enforcement,
  healthy local Nemotron Lightning inference, and five live autocomplete results
  for `1600 W Northwest Blvd, Spokane, WA`.
- Refreshed the previously exposed default Mapbox token and repeated the live
  autocomplete probe successfully using the dedicated token.
- Removed the exact temporary token-transfer files from the presenter Mac, GN100
  host, and NemoClaw sandbox. C13 remains open only for the unused DeepSeek key.

### 2026-08-15 - Independent left-rail scrolling

- Prevented result cards from flex-shrinking into the available viewport and
  made the left details rail the single vertical scroll container.
- Added a stable scrollbar gutter, a visible minimum thumb size, and an
  accessible `Evacuation details` landmark without changing the fixed map pane.
- Added two layout regression tests; all 22 frontend tests and the production
  build pass.
- Deployed the rebuilt frontend into the contained GN100 runtime. With six
  populated cards, the rail measured 2,073 px of content in a 541 px viewport;
  a 620 px rail scroll left the map bounds unchanged.

### 2026-08-15 - Final containment and dependency-age correction

- Restored the latest live FastAPI runtime inside the `my-assistant` sandbox;
  the persistent localhost OpenShell forward on port 8811 now feeds the
  Tailscale demo URL. Both endpoints report `mode=live` and
  `policy_enforced=true`.
- Confirmed active OpenShell policy revision 15, hash `a3e683a817a7`, and the
  exact local model `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4`.
- Replaced three exact dependency releases newer than the two-week cutoff:
  Uvicorn 0.52.3 → 0.52.0, OpenAI Python 3.1.0 → 2.52.0, and Motion 13.1.0 →
  12.43.0. Regenerated both dependency locks from clean installs and recorded
  exact-version release dates from the official registries.
- Tested a stricter pre-cutoff resolution for every npm transitive patch, then
  rejected it because it forced a `nanoid` version with a known high-severity
  advisory. The final lock keeps the pre-cutoff direct pins and secure current
  transitive patches; `npm audit --audit-level=low` reports zero findings.
- Passed 111 backend tests, 25 frontend tests, the production build, `pip
  check`, and the complete 14-stage replay verifier through a second contained
  service. The live demo remained available during verification.
- Ignored AppleDouble metadata and the unused `data/training/` experiment so
  neither can enter the submission accidentally; no user artifact was deleted.
- Corrected the documentation's test count, exact model ID, policy revision,
  contained runtime topology, dependency-age evidence, and public-repository
  readiness claim.

## Change rule

Move an item to `PASS` only after its exit criterion is demonstrated by a live
command, test, exported policy, or submission artifact. Configuration intent or
a UI badge alone is not evidence.
