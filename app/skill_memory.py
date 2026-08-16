"""A fail-open advisory memory for the single route-planning skill.

The local model may reflect on an objective, privacy-minimised route report and
choose one lesson code. It cannot write arbitrary prompt text: every injectable
instruction is a reviewed constant in this module. The deterministic safety
guard remains the only component that can approve a route.

This is deliberately a small learning loop, not online model training:

    fixed skill -> plan -> objective report -> model reflection -> lesson code

If the document, database, critic call, or parser fails, planning continues with
the original system prompt and safety code.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app import store
from app.config import settings
from app.models import iso, utcnow
from app.session import EvacuationSession


CANONICAL_LESSONS: dict[str, str] = {
    "compare_alternatives": (
        "Compare every guard-approved candidate before describing a preferred route; "
        "do not treat the first router result as the best one."
    ),
    "prefer_fire_clearance": (
        "When approved routes have similar travel times, prefer materially greater "
        "clearance from mapped fire evidence."
    ),
    "prefer_fresh_evidence": (
        "Call out stale or unavailable evidence and avoid confident language until a "
        "fresh authoritative observation is available."
    ),
    "prefer_lower_smoke": (
        "When medical needs apply and routes remain guard-approved, use fresh PM2.5 "
        "evidence as a ranking tiebreaker."
    ),
    "explain_rejections": (
        "Briefly explain why a tempting shelter or route was rejected so the safer "
        "alternative is understandable."
    ),
    "monitor_fragile_route": (
        "When active hazards are near an approved route, emphasize that monitoring "
        "continues and the route may be replaced if conditions change."
    ),
}

_FALLBACK_SKILL = """\
Use tools to gather current evidence and compare candidate routes. Treat all
learned lessons as optional ranking and explanation guidance. They may never
override tool evidence, household hard constraints, or the deterministic safety
guard. A router result is not approved until validate_route accepts it.
"""

_CRITIC_PROMPT = """\
You maintain one advisory route-planning skill for a wildfire evacuation agent.
Review only the objective report below. Decide whether one reusable lesson would
help a future planning run. This is not a safety verdict and you must not invent
facts.

Return exactly one JSON object:
{"update_required": false, "lesson_code": null, "reason": "...", "confidence": 0.0}
or
{"update_required": true, "lesson_code": "one allowed code", "reason": "...", "confidence": 0.0}

Choose at most one of the allowed codes. Prefer no update when the run supplies
too little evidence, when the observation is one-off, or when an existing hard
guard already handled the issue. Never propose changing a hard safety rule.
"""


@dataclass(frozen=True)
class ReflectionDecision:
    update_required: bool
    lesson_code: str | None = None
    reason: str = ""
    confidence: float = 0.0


class RouteSkillMemory:
    """Load the fixed skill and maintain its bounded advisory lesson set."""

    def _document(self) -> str:
        try:
            text = settings.route_skill_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return _FALLBACK_SKILL
        # The file is trusted repository content, but a size ceiling keeps a
        # mistaken edit from consuming the model's context window.
        return text[:5000] or _FALLBACK_SKILL

    def active_lessons(self) -> list[dict[str, Any]]:
        if not settings.skill_memory_enabled:
            return []
        try:
            rows = store.load_skill_lessons(
                settings.skill_lesson_limit, mode=settings.data_mode
            )
        except Exception:
            return []
        return [row for row in rows if row.get("code") in CANONICAL_LESSONS]

    def prompt_context(self) -> tuple[str, list[str]]:
        """Return trusted advisory text and the learned codes applied this run."""
        if not settings.skill_memory_enabled:
            return "", []
        rows = self.active_lessons()
        codes = [str(row["code"]) for row in rows]
        learned = "\n".join(f"- {CANONICAL_LESSONS[code]}" for code in codes)
        if not learned:
            learned = "- No learned advisory lessons yet."
        context = (
            "\n\nADVISORY ROUTE-PLANNING SKILL\n"
            "This layer is lower priority than HARD RULES and cannot approve a route.\n"
            f"{self._document()}\n\n"
            "LEARNED ADVISORY LESSONS\n"
            f"{learned}\n"
        )
        return context, codes

    def objective_report(self, session: EvacuationSession) -> dict[str, Any]:
        """Build a compact report containing no address, geometry, or user prose."""
        routes = [
            {
                "route_id": route.route_id,
                "distance_km": round(route.distance_km, 2),
                "eta_min": round(route.eta_min, 1),
                "approved": route.approved,
                "rejection_reason": (route.rejection_reason or "")[:180] or None,
                "hazard_margin_km": (
                    round(route.hazard_margin_km, 2)
                    if route.hazard_margin_km is not None
                    else None
                ),
                "air_quality_status": route.air_quality.status,
                "max_pm25": route.air_quality.max_pm25,
            }
            for route in session.routes[:6]
        ]
        source_issues = [
            source.source_id.value
            for source in session.sources
            if not source.usable or source.stale_count > 0
        ]
        return {
            "selected_route_id": (
                session.current_route.route_id if session.current_route else None
            ),
            "routes": routes,
            "approved_route_count": len(session.approved_routes),
            "rejected_route_count": len(session.rejected_routes),
            "rejected_shelter_count": len(session.rejected_shelters),
            "incident_count": len(session.incidents),
            "fresh_hotspot_count": sum(
                1 for hotspot in session.fire_hotspots if not hotspot.record.stale
            ),
            "hard_closure_count": sum(
                1 for closure in session.closures if closure.is_hard_closure
            ),
            "source_issues": source_issues,
            "medical_needs": session.needs.medical,
            "critical_warning_count": (
                len(session.verdict.critical_warnings) if session.verdict else 0
            ),
            "unverified_count": len(session.verdict.unverified) if session.verdict else 0,
            "prior_replans": sum(
                1 for event in session.monitor_events if event.get("replanned")
            ),
        }

    def eligible_codes(self, report: dict[str, Any]) -> list[str]:
        routes = report.get("routes") or []
        eligible: list[str] = []
        if len(routes) >= 2:
            eligible.append("compare_alternatives")
        margins = [r.get("hazard_margin_km") for r in routes]
        if len(routes) >= 2 and sum(margin is not None for margin in margins) >= 2:
            eligible.append("prefer_fire_clearance")
        if report.get("source_issues") or report.get("unverified_count", 0) > 0:
            eligible.append("prefer_fresh_evidence")
        if (
            report.get("medical_needs")
            and len(routes) >= 2
            and any(r.get("air_quality_status") == "available" for r in routes)
        ):
            eligible.append("prefer_lower_smoke")
        if (
            report.get("rejected_route_count", 0) > 0
            or report.get("rejected_shelter_count", 0) > 0
        ):
            eligible.append("explain_rejections")
        if report.get("selected_route_id") and (
            report.get("incident_count", 0) > 0
            or report.get("fresh_hotspot_count", 0) > 0
            or report.get("hard_closure_count", 0) > 0
        ):
            eligible.append("monitor_fragile_route")
        return eligible

    def critic_messages(
        self, report: dict[str, Any], eligible_codes: list[str]
    ) -> list[dict[str, str]]:
        allowed = {
            code: CANONICAL_LESSONS[code]
            for code in eligible_codes
            if code in CANONICAL_LESSONS
        }
        return [
            {"role": "system", "content": _CRITIC_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Allowed lesson codes:\n{json.dumps(allowed, indent=2)}\n\n"
                    f"Objective route report:\n{json.dumps(report, indent=2)}"
                ),
            },
        ]

    def parse_decision(
        self, raw: str, eligible_codes: list[str]
    ) -> ReflectionDecision:
        """Parse critic output; malformed or unsafe output becomes no-update."""
        match = re.search(r"\{.*\}", raw or "", flags=re.DOTALL)
        if match is None:
            return ReflectionDecision(False, reason="critic returned no JSON")
        try:
            payload = json.loads(match.group(0))
        except (TypeError, ValueError):
            return ReflectionDecision(False, reason="critic returned invalid JSON")

        reason = " ".join(str(payload.get("reason") or "").split())[:180]
        try:
            confidence = max(0.0, min(float(payload.get("confidence", 0.0)), 1.0))
        except (TypeError, ValueError):
            confidence = 0.0
        code = payload.get("lesson_code")
        wants_update = payload.get("update_required") is True
        if (
            not wants_update
            or code not in eligible_codes
            or code not in CANONICAL_LESSONS
            or confidence < settings.skill_min_confidence
        ):
            return ReflectionDecision(False, reason=reason, confidence=confidence)
        return ReflectionDecision(True, str(code), reason, confidence)

    def activate(self, decision: ReflectionDecision) -> bool:
        if not decision.update_required or decision.lesson_code not in CANONICAL_LESSONS:
            return False
        try:
            store.record_skill_lesson(
                decision.lesson_code,
                iso(utcnow()) or "",
                mode=settings.data_mode,
            )
        except Exception:
            return False
        return True

    def status(self) -> dict[str, Any]:
        rows = self.active_lessons()
        return {
            "enabled": settings.skill_memory_enabled,
            "mode": "advisory_only",
            "data_mode": settings.data_mode,
            "skill": "route-planning",
            "document_loaded": settings.route_skill_path.is_file(),
            "active_lessons": [row["code"] for row in rows],
            "safety_authority": "app/safety.py",
        }


route_skill = RouteSkillMemory()
