"""Fixture-backed egress for `EVAC_DATA_MODE=replay`.

Replay is intercepted at the **egress layer**, not inside each source module.
Every parser, every geometry operation and every safety gate above this point
runs exactly the code it runs against live data — the only thing that changes is
where the bytes come from. A replay path that bypassed the parsers would test
nothing and would drift from live within a day.

Two things are deliberately *not* replayed:

- `policy_probe` requests (the fire camera). The containment demonstration has
  to be a real refusal from OpenShell in every mode, or it is theatre.
- Anything with no fixture. Missing fixtures surface as `UPSTREAM_ERROR`, not as
  empty success, so a gap in the scenario looks like a gap and not like an
  all-clear.

Fixtures live in `data/fixtures/` and are matched by URL substring. Each carries
a `_meta` block recording whether it was captured live or authored for the
scenario; `app/sources/*` never sees `_meta`, and the UI shows it.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import REPO_ROOT
from app.egress import EgressResult, Outcome

FIXTURE_DIR = REPO_ROOT / "data" / "fixtures"
MANIFEST = FIXTURE_DIR / "manifest.json"


# The scenario phase. The demo trigger advances it to "after", which changes
# what the *sources* return — it does not tell the monitor what to think. The
# monitor still has to fetch, compare against stored state, and work out for
# itself that its route died. That keeps the replan a real recalculation on the
# same code path that live data would exercise.
_phase: str = "before"


def set_phase(phase: str) -> None:
    global _phase
    _phase = phase


def get_phase() -> str:
    return _phase


@lru_cache(maxsize=1)
def _manifest() -> list[dict[str, Any]]:
    if not MANIFEST.exists():
        return []
    try:
        data = json.loads(MANIFEST.read_text())
    except (OSError, ValueError):
        return []
    entries = data.get("entries", [])
    # Longest match wins, so a specific layer beats a generic host rule.
    return sorted(entries, key=lambda e: -len(e.get("match", "")))


@lru_cache(maxsize=64)
def _load(filename: str) -> str | None:
    path = FIXTURE_DIR / filename
    if not path.exists():
        return None
    try:
        return path.read_text()
    except OSError:
        return None


def scenario_meta() -> dict[str, Any]:
    if not MANIFEST.exists():
        return {}
    try:
        return json.loads(MANIFEST.read_text()).get("scenario", {})
    except (OSError, ValueError):
        return {}


def is_scenario_query(query: str) -> bool:
    """Whether an address is asking for the authored Rifle Club replay.

    The demo needs captured origin and route geometry to remain internally
    consistent with its authored closures. Other Spokane addresses must not be
    silently snapped to that origin; they continue through live geocoding and
    routing when ``EVAC_LIVE_LOCATION_IN_REPLAY`` is enabled.
    """
    normalized = " ".join((query or "").lower().replace("-", " ").split())
    return "rifle club" in normalized


def clear_cache() -> None:
    _manifest.cache_clear()
    _load.cache_clear()


def lookup(url: str) -> EgressResult | None:
    """Return a fixture-backed result for `url`, or None if none matches.

    A phase-specific entry wins over a phase-agnostic one for the same URL, so
    advancing the phase swaps a source's answer without touching any other
    fixture.
    """
    entries = _manifest()
    phased = [e for e in entries if e.get("phase") == _phase]
    for entry in phased + [e for e in entries if not e.get("phase")]:
        match = entry.get("match", "")
        if not match or match not in url:
            continue

        # An entry may pin extra query fragments so that two queries against the
        # same layer (say incidents vs. perimeters) resolve to different files.
        requires = entry.get("requires") or []
        if any(frag not in url for frag in requires):
            continue

        body = _load(entry["file"])
        if body is None:
            continue

        payload = _strip_meta(body)
        return EgressResult(
            outcome=Outcome.REPLAY,
            url=url,
            host=entry.get("host", ""),
            status=200,
            body=payload,
            elapsed_ms=entry.get("latency_ms", 0),
        )

    return None


def _strip_meta(body: str) -> str:
    """Remove the `_meta` block so sources parse an authentic upstream shape.

    A fixture whose upstream answers with a bare array (Nominatim) is stored
    boxed under `_list`; unbox it here so the source sees the array it expects.
    """
    try:
        data = json.loads(body)
    except ValueError:
        return body
    if isinstance(data, dict):
        if "_list" in data:
            data = data["_list"]
        elif "_meta" in data:
            data = {k: v for k, v in data.items() if k != "_meta"}
    return json.dumps(data)


def fixture_provenance(url: str) -> dict[str, Any] | None:
    """The `_meta` block for whichever fixture serves `url`, for the UI."""
    for entry in _manifest():
        match = entry.get("match", "")
        if not match or match not in url:
            continue
        if any(frag not in url for frag in (entry.get("requires") or [])):
            continue
        body = _load(entry["file"])
        if body is None:
            continue
        try:
            return (json.loads(body) or {}).get("_meta")
        except ValueError:
            return None
    return None
