"""The only module in the agent that touches the network.

Every outbound request is dispatched as `nemoclaw <sandbox> exec -- curl ...` so
that the sandbox's OpenShell policy governs it. The policy lives outside this
process and the agent cannot edit it; that is the point.

Each call resolves to exactly one `Outcome`, and each outcome maps to distinct
user-facing language. A policy denial is never retried, never worked around, and
never silently degraded into "no hazard found" — see DESIGN sections 2.1 and 6.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import urlencode, urlsplit

from app.config import ALLOWED_HOSTS, settings

# curl exits 56 when the CONNECT tunnel is refused, which is how a *host*-level
# denial reaches us: the proxy never opens the tunnel, so there is no HTTP status
# to read. Verified signature in docs/SOURCES.md.
_CURL_RECV_ERROR = 56
_TUNNEL_DENIED = re.compile(r"CONNECT tunnel failed, response 403", re.I)
# curl prefixes every error it reports with `curl: (N) ...`. Its presence is how
# we tell an upstream/transport failure from the sandbox harness failing to run.
_CURL_ERROR_LINE = re.compile(r"curl: \(\d+\)[^\n]*")

# A *path*- or *method*-level denial looks completely different: the tunnel opens
# and the L7 proxy answers the request itself with a structured JSON body.
_L7_DENIAL_MARKER = '"error":"policy_denied"'

_STATUS_SENTINEL = "\n__EVAC_HTTP__:"
_SENSITIVE_QUERY_VALUE = re.compile(
    r"([?&](?:access_token|api_key|key|token)=)[^&#]*", re.I
)


def _redact_url(url: str) -> str:
    """Return a log/persistence-safe URL without changing the real request."""
    return _SENSITIVE_QUERY_VALUE.sub(r"\1[REDACTED]", url)


class Outcome(str, Enum):
    OK = "OK"
    POLICY_DENIED = "POLICY_DENIED"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    SANDBOX_UNAVAILABLE = "SANDBOX_UNAVAILABLE"
    REPLAY = "REPLAY"


@dataclass(frozen=True)
class Denial:
    """What the policy refused, in the policy's own words."""

    host: str
    method: str
    path: str
    policy: str | None = None
    rule: str | None = None
    detail: str | None = None
    layer: str = "l7"
    binary: str | None = None

    @property
    def summary(self) -> str:
        where = self.policy or "sandbox egress policy"
        return f"{self.method} {self.host}{self.path} not permitted by {where}"


@dataclass
class EgressResult:
    outcome: Outcome
    url: str
    host: str
    status: int | None = None
    body: str = ""
    error: str | None = None
    denial: Denial | None = None
    elapsed_ms: int = 0
    fetched_at: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        """True when the result carries a usable payload.

        REPLAY counts: a fixture is served precisely so the parsers above run
        unchanged. The distinction between live and replayed is preserved in
        `outcome` and reported in the sources panel — it just is not a failure.
        """
        return self.outcome in (Outcome.OK, Outcome.REPLAY)

    def json(self) -> Any:
        """Parsed body, or None when it is not JSON.

        A source that cannot parse its own payload must degrade to
        UPSTREAM_ERROR rather than raise — an exception here would look, three
        layers up, exactly like "there is no hazard".
        """
        if not self.body:
            return None
        try:
            return json.loads(self.body)
        except (ValueError, TypeError):
            return None


def _parse_l7_denial(body: str, host: str, method: str, path: str) -> Denial:
    try:
        d = json.loads(body)
    except (ValueError, TypeError):
        return Denial(host=host, method=method, path=path)
    return Denial(
        host=d.get("host", host),
        method=d.get("method", method),
        path=d.get("path", path),
        policy=d.get("policy"),
        rule=d.get("rule"),
        detail=d.get("detail"),
        layer=d.get("layer", "l7"),
        binary=d.get("binary"),
    )


class Egress:
    """Bounded, policy-governed fetcher.

    Concurrency is capped because every call is a subprocess. Nominatim gets a
    separate serial lane so we honour its 1 req/s rule regardless of what the
    rest of the agent is doing.
    """

    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(settings.egress_concurrency)
        self._nominatim_lock = asyncio.Lock()
        self._nominatim_last = 0.0

    async def fetch(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        method: str = "GET",
        timeout: float = 30.0,
        policy_probe: bool = False,
        bypass_replay: bool = False,
    ) -> EgressResult:
        """Fetch `url` through the sandbox.

        `policy_probe` skips the in-process allowlist mirror so that the sandbox
        is the thing that refuses. Exactly one caller sets it —
        `app/sources/firecam.py` — because a blocked action the agent declined
        to attempt proves nothing about containment. The request is really made,
        and OpenShell really denies it.
        """
        if params:
            url = f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"

        split = urlsplit(url)
        host, path = split.hostname or "", split.path or "/"

        # Replay serves recorded bytes, but never for a policy probe: the
        # containment demonstration must be a real refusal from OpenShell in
        # every mode. A fixture that said "blocked" would prove nothing.
        if settings.replay and not policy_probe and not bypass_replay:
            from app import replay

            fixture = replay.lookup(url)
            if fixture is not None:
                fixture.url = _redact_url(fixture.url)
                return fixture
            # No fixture is a coverage gap, not an empty success.
            return EgressResult(
                outcome=Outcome.UPSTREAM_ERROR,
                url=_redact_url(url),
                host=host,
                error=f"no replay fixture for {host}{path}",
            )

        # Refuse an unlisted host in process, before spawning anything. In the
        # sandbox the proxy would refuse it anyway; this makes the degraded
        # host-direct mode obey the same allowlist, where nothing else would.
        if host not in ALLOWED_HOSTS and not policy_probe:
            return EgressResult(
                outcome=Outcome.POLICY_DENIED,
                url=_redact_url(url),
                host=host,
                denial=Denial(
                    host=host,
                    method=method,
                    path=path,
                    policy="spokane_evac",
                    detail=f"{host} is not in the agent's allowlist",
                ),
            )

        if host == "nominatim.openstreetmap.org":
            async with self._nominatim_lock:
                wait = 1.0 - (time.monotonic() - self._nominatim_last)
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    return await self._dispatch(url, host, path, method, timeout)
                finally:
                    self._nominatim_last = time.monotonic()

        async with self._sem:
            return await self._dispatch(url, host, path, method, timeout)

    async def _dispatch(
        self, url: str, host: str, path: str, method: str, timeout: float
    ) -> EgressResult:
        started = time.monotonic()

        curl = [
            "curl",
            "-sS",
            "-m",
            str(int(timeout)),
            "-A",
            settings.user_agent,
            "-X",
            method,
            "-w",
            f"{_STATUS_SENTINEL}%{{http_code}}",
            url,
        ]

        if settings.inside_openshell:
            # The complete harness is already executing inside the OpenShell
            # sandbox. A direct child process remains inside that boundary and
            # is governed by the active binary/host/path/method policy. Calling
            # NemoClaw again here would attempt to nest one sandbox in another.
            argv = curl
            sandboxed = True
        elif shutil.which(settings.nemoclaw_bin) or _exists(settings.nemoclaw_bin):
            argv = [settings.nemoclaw_bin, settings.sandbox, "exec", "--", *curl]
            sandboxed = True
        elif settings.allow_host_direct_fallback and host in ALLOWED_HOSTS:
            # Only reachable when the sandbox binary is missing entirely. A
            # policy *denial* must never land here, and neither may a
            # policy_probe host: re-checking ALLOWED_HOSTS is what stops the
            # degraded mode from becoming a way around the boundary.
            argv = curl
            sandboxed = False
        else:
            return EgressResult(
                outcome=Outcome.SANDBOX_UNAVAILABLE,
                url=_redact_url(url),
                host=host,
                error=f"nemoclaw not found at {settings.nemoclaw_bin}",
            )

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            raw_out, raw_err = await asyncio.wait_for(
                proc.communicate(), timeout=timeout + 20
            )
        except asyncio.TimeoutError:
            return EgressResult(
                outcome=Outcome.UPSTREAM_ERROR,
                url=_redact_url(url),
                host=host,
                error=f"timed out after {timeout}s",
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        except (OSError, FileNotFoundError) as exc:
            return EgressResult(
                outcome=Outcome.SANDBOX_UNAVAILABLE,
                url=_redact_url(url),
                host=host,
                error=str(exc),
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )

        elapsed = int((time.monotonic() - started) * 1000)
        stdout = raw_out.decode("utf-8", "replace")
        stderr = raw_err.decode("utf-8", "replace")
        rc = proc.returncode or 0

        body, status = _split_status(stdout)

        return classify(
            url=_redact_url(url),
            host=host,
            path=path,
            method=method,
            returncode=rc,
            body=body,
            status=status,
            stderr=stderr,
            elapsed_ms=elapsed,
            sandboxed=sandboxed,
        )


def _exists(p: str) -> bool:
    from pathlib import Path

    return Path(p).exists()


def _split_status(stdout: str) -> tuple[str, int | None]:
    """Peel the `-w` sentinel off the tail of the body."""
    idx = stdout.rfind(_STATUS_SENTINEL)
    if idx == -1:
        return stdout, None
    body = stdout[:idx]
    tail = stdout[idx + len(_STATUS_SENTINEL) :].strip()
    try:
        code = int(tail)
    except ValueError:
        return body, None
    return body, (code or None)


def classify(
    *,
    url: str,
    host: str,
    path: str,
    method: str,
    returncode: int,
    body: str,
    status: int | None,
    stderr: str,
    elapsed_ms: int = 0,
    sandboxed: bool = True,
) -> EgressResult:
    """Map a completed subprocess to exactly one outcome.

    Pure and side-effect free so the classification tests can drive it from
    captured fixtures with no sandbox and no network.
    """
    base = dict(url=url, host=host, elapsed_ms=elapsed_ms)

    # Host-level denial: no tunnel, therefore no HTTP status at all.
    if returncode == _CURL_RECV_ERROR and _TUNNEL_DENIED.search(stderr):
        return EgressResult(
            outcome=Outcome.POLICY_DENIED,
            status=None,
            error="CONNECT tunnel refused by the sandbox egress policy",
            denial=Denial(
                host=host,
                method=method,
                path=path,
                policy="spokane_evac",
                detail=f"{host}:443 is not an allowed host",
                layer="connect",
            ),
            **base,
        )

    # Path- or method-level denial: the tunnel opened and the L7 proxy answered.
    if status == 403 and _L7_DENIAL_MARKER in body.replace(" ", ""):
        return EgressResult(
            outcome=Outcome.POLICY_DENIED,
            status=403,
            body=body,
            error="request refused by the sandbox egress policy",
            denial=_parse_l7_denial(body, host, method, path),
            **base,
        )

    # Something failed before we got a status. Deciding *who* failed matters:
    # "the sandbox is down" and "the upstream is down" are different sentences
    # to a resident, and only one of them is our problem to fix.
    #
    # The discriminator is whether curl itself reported an error. It always
    # prefixes those with `curl: (N)`. Matching on the word "nemoclaw" does not
    # work — the wrapper prints an `Active gateway set to 'nemoclaw'` banner to
    # stderr on every single call, which would make every curl failure look like
    # a dead sandbox.
    if returncode != 0 and status is None:
        curl_spoke = _CURL_ERROR_LINE.search(stderr)
        if curl_spoke or not sandboxed:
            return EgressResult(
                outcome=Outcome.UPSTREAM_ERROR,
                error=(curl_spoke.group(0) if curl_spoke else stderr.strip()[:400])
                or f"curl exit {returncode}",
                **base,
            )
        return EgressResult(
            outcome=Outcome.SANDBOX_UNAVAILABLE,
            error=stderr.strip()[:400] or f"exit {returncode}",
            **base,
        )

    if status is not None and 200 <= status < 300:
        return EgressResult(outcome=Outcome.OK, status=status, body=body, **base)

    return EgressResult(
        outcome=Outcome.UPSTREAM_ERROR,
        status=status,
        body=body,
        error=f"upstream returned HTTP {status}" if status else "no response",
        **base,
    )


egress = Egress()
