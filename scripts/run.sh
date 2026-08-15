#!/usr/bin/env bash
# Start the evacuation agent. Defaults to the replay scenario, which is what the
# demo opens in; pass --live to fetch from the real sources through the sandbox.
#
#   ./scripts/run.sh              # replay, single port, built UI
#   ./scripts/run.sh --live       # live data through the OpenShell policy
#   ./scripts/run.sh --dev        # vite dev server on :5173 with hot reload
#
set -euo pipefail

cd "$(dirname "$0")/.."

MODE="replay"
DEV=0
PORT="${EVAC_PORT:-8811}"

for arg in "$@"; do
  case "$arg" in
    --live) MODE="live" ;;
    --replay) MODE="replay" ;;
    --dev) DEV=1 ;;
    *) echo "unknown option: $arg" >&2; exit 1 ;;
  esac
done

if [[ ! -x .venv/bin/python ]]; then
  echo "No virtualenv. Run:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

# The policy must be applied for the containment demonstration to mean anything.
if ! "${EVAC_NEMOCLAW_BIN:-$HOME/.local/bin/nemoclaw}" "${EVAC_SANDBOX:-my-assistant}" \
      policy-list 2>/dev/null | grep -q "spokane-evac"; then
  echo "!! The spokane-evac policy is not applied to the sandbox."
  echo "   The blocked-action demonstration will not be meaningful without it:"
  echo "     nemoclaw ${EVAC_SANDBOX:-my-assistant} policy-add --from-file ./policies/spokane-evac.yaml --yes"
  echo
fi

export EVAC_DATA_MODE="$MODE"

if [[ "$DEV" == "1" ]]; then
  echo "API   → http://127.0.0.1:${PORT}   (mode: ${MODE})"
  echo "UI    → http://127.0.0.1:5173      (vite dev, proxying /api)"
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --reload &
  API_PID=$!
  trap 'kill $API_PID 2>/dev/null || true' EXIT
  cd web && npm run dev
else
  if [[ ! -d web/dist ]]; then
    echo "Building the UI…"
    (cd web && npm install --silent && npm run build)
  fi
  echo
  echo "  Always-On Wildfire Evacuation Agent"
  echo "  mode: ${MODE}"
  echo "  open: http://127.0.0.1:${PORT}"
  echo
  exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
fi
