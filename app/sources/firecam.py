"""Fire-camera stills — a real capability that the policy refuses.

This is a genuine tool against a genuine host. ALERTWildfire is the camera
network fire agencies actually use for visual confirmation of fire position, and
`cameras.alertwildfire.org` resolves and serves. It is simply absent from
`policies/spokane-evac.yaml`, so OpenShell's L7 proxy refuses the CONNECT and
the agent reports a blocked action.

Nothing here simulates the denial. There is no `if demo: return blocked` branch,
and there could not be one that would prove anything — the enforcement is
external to this process and the agent cannot edit the policy that produces it.

The host matters. An earlier version pointed at a placeholder domain, which
meant the refusal was unfalsifiable: a sceptic could object that a non-existent
host fails whether or not a policy exists, and they would have been right. With
a host that genuinely answers, the demonstration is a real toggle — add it to
the policy and the tool works, remove it and the tool is refused — and that
toggle is verified in `docs/DEMO.md`.
"""

from __future__ import annotations

from app.egress import EgressResult, Outcome, egress
from app.models import BlockedAction

HOST = "cameras.alertwildfire.org"
_URL = f"https://{HOST}/api/firecams/v0/cameras"


async def get_fire_camera(lat: float, lon: float, radius_km: float = 25.0) -> EgressResult:
    """Attempt to pull the nearest fire-camera still for visual confirmation.

    `policy_probe=True` bypasses the in-process allowlist mirror so the refusal
    comes from the sandbox rather than from a local short-circuit. A block the
    agent decided for itself would demonstrate nothing about containment.
    """
    return await egress.fetch(
        _URL,
        params={"lat": lat, "lon": lon, "radius_km": radius_km},
        policy_probe=True,
        timeout=15.0,
    )


def to_blocked_action(result: EgressResult, tool: str = "get_fire_camera") -> BlockedAction | None:
    if result.outcome is not Outcome.POLICY_DENIED:
        return None
    d = result.denial
    return BlockedAction(
        tool=tool,
        host=d.host if d else HOST,
        method=d.method if d else "GET",
        path=d.path if d else "/api/firecams/v0/cameras",
        policy=d.policy if d else None,
        rule=d.rule if d else None,
        detail=(d.detail if d else None) or result.error,
        layer=d.layer if d else "connect",
    )
