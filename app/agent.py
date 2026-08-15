"""The tool-calling loop against the local Nemotron NIM.

The model's job is to read intent, choose tools, and write prose. It does not
decide anything safety-bearing: after the loop ends, `app/safety.py` produces
the verdict from the records the tools gathered, and the model's text is
rendered *around* that verdict rather than in place of it.

Two consequences follow, and both are enforced here rather than requested in the
prompt:

- If the model skips a required lookup, `_backfill` runs it anyway and the trace
  says the guard did it.
- Whatever the model writes, `Verdict` comes from the guard. A model that
  hallucinated an all-clear would change the wording of the page and none of
  its decisions.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from openai import AsyncOpenAI

from app import tools
from app.config import settings
from app.models import StepKind
from app.safety import decide
from app.session import EvacuationSession
from app.tools import TOOL_SCHEMAS

MAX_TURNS = 8

SYSTEM_PROMPT = """\
You are the orchestration agent for a wildfire evacuation system serving \
residents of Spokane County, Washington.

GOAL
Move the user's evacuation task toward completion. Do not merely answer the \
question they asked.

HARD RULES
1. Treat live safety state as tool-grounded data only. Never state an \
evacuation level, road closure, shelter status or fire boundary that did not \
come back from a tool in this conversation. You have no memory of current \
conditions.
2. Always call get_evacuation_status before telling anyone to stay or go.
3. Before recommending any route you must call plan_safe_route AND then \
validate_route. A route a router returned is not a safe route.
4. If sources disagree, say so plainly and lower your confidence. Never resolve \
a conflict silently.
5. Apply household constraints (pets, mobility, medical) as hard filters. Never \
trade a hard requirement against a shorter drive.
6. If a tool reports blocked:true, the sandbox egress policy refused it. That is \
final. Do not retry it, do not look for another route to the same data, and do \
not pretend you have the information. Report it as unavailable and continue.
7. Never recommend returning home without an explicit clearance from \
check_hazmat_clearance. A downgrade is not a clearance.
8. If data is missing or stale, say the recommendation cannot be fully verified.

RESPONSE STYLE
Lead with the action. Then the destination, then the route, then why it was \
chosen and which sources were checked. Be concise — a person reading this may \
be packing a car. Use plain language, short sentences, no headers, no bullet \
symbols, under 180 words.

Do not describe your reasoning process or narrate which tools you are about to \
call. The interface already shows the user every tool call, its arguments and \
its result.
"""

# Lookups the guard performs itself if the model did not (book: the deterministic
# layer fills in, and the trace says so).
REQUIRED = ("get_evacuation_status", "get_active_incidents", "get_closures")


class Agent:
    def __init__(self) -> None:
        self.client = AsyncOpenAI(
            base_url=settings.inference_base_url,
            api_key=settings.inference_api_key,
            timeout=120.0,
            max_retries=1,
        )

    async def run(self, session: EvacuationSession, user_message: str) -> EvacuationSession:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._frame(session, user_message)},
        ]

        called: set[str] = set()
        prose: str | None = None

        for turn in range(MAX_TURNS):
            started = time.monotonic()
            try:
                completion = await self.client.chat.completions.create(
                    model=settings.inference_model,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    tool_choice="auto",
                    temperature=0.2,
                    max_tokens=900,
                )
            except Exception as exc:
                session.step(
                    StepKind.MODEL,
                    "Model unavailable",
                    detail=str(exc)[:300],
                    outcome="error",
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
                break

            latency = int((time.monotonic() - started) * 1000)
            choice = completion.choices[0]
            msg = choice.message
            tool_calls = msg.tool_calls or []

            if tool_calls:
                session.step(
                    StepKind.MODEL,
                    f"Model selected {len(tool_calls)} tool"
                    f"{'s' if len(tool_calls) > 1 else ''}",
                    detail=", ".join(tc.function.name for tc in tool_calls),
                    outcome="tool_calls",
                    latency_ms=latency,
                )
            else:
                prose = (msg.content or "").strip()
                session.step(
                    StepKind.MODEL,
                    "Model wrote the resident-facing summary",
                    outcome="content",
                    latency_ms=latency,
                )
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )

            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except ValueError:
                    args = {}

                result, ms_taken = await tools.call(session, name, args)
                called.add(name)
                self._trace_tool(session, name, args, result, ms_taken)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str)[:6000],
                    }
                )

        await self._backfill(session, called)
        self._finalize(session, prose)
        return session

    # --- helpers -------------------------------------------------------------

    def _frame(self, session: EvacuationSession, user_message: str) -> str:
        where = session.query or "unknown"
        if session.place is not None:
            where = f"{session.place.label} ({session.place.lat:.5f}, {session.place.lon:.5f})"
        return (
            f"Location: {where}\n"
            f"Household: {session.needs.describe()}\n"
            f"Approved notification contacts: "
            f"{', '.join(session.approved_contacts) or 'none'}\n\n"
            f"Question: {user_message}"
        )

    def _trace_tool(
        self,
        session: EvacuationSession,
        name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        latency_ms: int,
    ) -> None:
        blocked = bool(result.get("blocked")) or result.get("status") == "blocked"
        kind = StepKind.BLOCKED if blocked else StepKind.TOOL

        if blocked:
            detail = result.get("reason") or result.get("detail") or "refused by policy"
            outcome = "BLOCKED"
        elif "error" in result:
            detail = str(result["error"])
            outcome = "error"
        else:
            detail = self._summarize(name, result)
            outcome = "ok"

        session.step(
            kind,
            name,
            detail=detail,
            arguments=args or None,
            outcome=outcome,
            latency_ms=latency_ms,
        )

    def _summarize(self, name: str, result: dict[str, Any]) -> str:
        """One line for the activity panel — outputs, never deliberation."""
        if name == "geocode":
            return f"resolved to {result.get('label', '?')}"
        if name == "get_evacuation_status":
            src = ", ".join(result.get("sources_checked") or []) or "none"
            return (
                f"{result.get('level')} · {len(result.get('sources_checked') or [])} "
                f"sources checked ({src}) · consensus="
                f"{result.get('consensus')} · confidence={result.get('confidence')}"
            )
        if name == "get_active_incidents":
            n = result.get("count", 0)
            first = (result.get("incidents") or [{}])[0]
            return (
                f"{n} nearby · closest {first.get('name', '—')} at "
                f"{first.get('distance_km', '?')} km"
                if n
                else "no incidents returned"
            )
        if name == "find_shelters":
            pref = (result.get("preferred_shelter") or {}).get("name")
            rej = len(result.get("rejected") or [])
            return f"preferred {pref or 'none'} · {rej} rejected on hard constraints"
        if name == "get_closures":
            return (
                f"{len(result.get('hard_closures') or [])} hard closures, "
                f"{result.get('alerts', 0)} alerts"
            )
        if name == "plan_safe_route":
            return f"{len(result.get('candidates') or [])} candidates to {result.get('destination')}"
        if name == "validate_route":
            sel = result.get("selected_route_id")
            rej = result.get("rejected_routes") or []
            bits = [f"{r['route_id']} rejected: {r['reason']}" for r in rej]
            return " · ".join(([f"{sel} approved"] if sel else ["none approved"]) + bits)
        if name == "check_hazmat_clearance":
            return f"clearance={result.get('cleared')} · safe_to_return={result.get('safe_to_return')}"
        if name == "send_notification":
            return f"{result.get('status')} → {result.get('contact', '?')}"
        return json.dumps(result, default=str)[:180]

    async def _backfill(self, session: EvacuationSession, called: set[str]) -> None:
        """Run the lookups the model skipped. The trace attributes them correctly."""
        for name in REQUIRED:
            if name in called:
                continue
            result, ms_taken = await tools.call(session, name, {})
            session.step(
                StepKind.GUARD,
                f"{name} (model omitted it; guard ran it)",
                detail=self._summarize(name, result) if "error" not in result else str(result["error"]),
                outcome="backfilled",
                latency_ms=ms_taken,
            )

        # A verdict that recommends a destination needs a validated route behind
        # it, whether or not the model asked for one.
        if session.routes and not session.approved_routes and not session.rejected_routes:
            result, ms_taken = await tools.call(session, "validate_route", {})
            session.step(
                StepKind.GUARD,
                "validate_route (guard enforced)",
                detail=self._summarize("validate_route", result),
                outcome="backfilled",
                latency_ms=ms_taken,
            )

    def _finalize(self, session: EvacuationSession, prose: str | None) -> None:
        """The guard writes the verdict. This is the only place it is set."""
        started = time.monotonic()
        ctx = tools._ctx(session)
        verdict, approved, rejected, eligible, rejected_shelters = decide(ctx)

        session.approved_routes = approved
        session.rejected_routes = rejected
        session.rejected_shelters = rejected_shelters
        session.current_route = approved[0] if approved else None
        if eligible:
            session.destination = eligible[0]
        session.verdict = verdict

        if prose:
            session.verdict.narrative = prose

        session.step(
            StepKind.GUARD,
            "Safety guard produced the verdict",
            detail=(
                f"{verdict.level.label} · {len(approved)} route(s) approved, "
                f"{len(rejected)} rejected · "
                f"{len(verdict.critical_warnings)} warning(s), "
                f"{len(verdict.unverified)} unverified item(s)"
            ),
            outcome="verdict",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        session.persist()


agent = Agent()
