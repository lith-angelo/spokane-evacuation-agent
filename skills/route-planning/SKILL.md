---
name: route-planning
version: 1.0.0
authority: advisory-only
---

# Route-planning skill

Use current tool evidence to compare evacuation route candidates and explain the
guard-approved result clearly. Consider travel time only after hard constraints
and route validation. When evidence is incomplete, say so.

This skill never approves a route. It may not weaken or replace evacuation-zone,
fire-perimeter, road-closure, household-needs, air-quality, freshness, or
re-entry checks in `app/safety.py`. If this skill is unavailable, continue with
the normal tool loop and deterministic safety guard.
