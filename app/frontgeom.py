"""Turning fire perimeters into something a model can regress on.

A polygon is an awkward prediction target: variable vertex count, no natural
ordering, no fixed dimensionality. This module converts a perimeter into a
**radial front profile** — the distance from the fire's centroid to its edge
along a fixed set of compass bearings.

That makes the learning problem a fixed-width regression (how far does the front
advance along each bearing over the next h hours?), and it makes the output
directly usable: add the predicted advances back onto the radii and you have a
predicted perimeter polygon again.

It also carries the right inductive bias. Fire spreads fastest downwind, and
"downwind" is a statement about a bearing, so a model indexed by bearing can
learn that relationship directly instead of having to discover it in raw
coordinates.

Shared by the training pipeline and by inference, so the features are computed
by identical code in both — a training/serving skew here would be invisible and
would quietly poison the forecast.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from app.geo import _KM_PER_DEGREE_LAT, _km_per_degree_lon, to_geometry

# 16 compass sectors, every 22.5 degrees. Enough to resolve a wind-driven lobe
# without making the target so wide that each sector sees little data.
N_BEARINGS = 16
BEARINGS = [i * (360.0 / N_BEARINGS) for i in range(N_BEARINGS)]

# How far to cast each ray when measuring the edge. Larger than any single
# perimeter we expect; the ray is clipped to the polygon anyway.
_RAY_KM = 400.0


def bearing_unit(deg: float) -> tuple[float, float]:
    """Unit vector for a compass bearing: 0 = north, 90 = east."""
    rad = math.radians(deg)
    return (math.sin(rad), math.cos(rad))  # (east, north)


def anchor_of(geom: Any) -> tuple[float, float] | None:
    """The (lat, lon) a profile is measured from.

    The centroid when it lies inside the polygon, and a representative interior
    point when it does not — for a crescent or multi-lobed burn the centroid can
    fall outside the burn entirely, and every radius measured from it would be
    meaningless.
    """
    g = to_geometry(geom)
    if g is None or g.is_empty:
        return None
    try:
        c = g.centroid
        if not g.covers(c):
            c = g.representative_point()
    except Exception:
        return None
    return (c.y, c.x)


def radial_profile(
    geom: Any, anchor: tuple[float, float] | None = None
) -> tuple[list[float], tuple[float, float]] | None:
    """(radii_km per bearing, (lat, lon) anchor) for a perimeter.

    `anchor` pins the origin the rays are cast from. Consecutive perimeters of
    the same fire **must** be profiled about a shared anchor — measuring each
    about its own centre would express the later perimeter relative to a moved
    origin, which cancels out the fire's translation and leaves the model
    unable to learn that a front advanced at all. Pass the earlier perimeter's
    anchor when building a training pair.

    Returns None when the geometry is unusable, so callers drop the sample
    rather than training on a degenerate shape.
    """
    g = to_geometry(geom)
    if g is None or g.is_empty:
        return None

    if anchor is None:
        anchor = anchor_of(g)
        if anchor is None:
            return None

    lat0, lon0 = anchor
    kx = _km_per_degree_lon(lat0) or 1e-9
    ky = _KM_PER_DEGREE_LAT

    radii: list[float] = []
    for b in BEARINGS:
        east, north = bearing_unit(b)
        # Cast in degrees scaled so the ray is metric-straight.
        dlon = (east * _RAY_KM) / kx
        dlat = (north * _RAY_KM) / ky
        ray = LineString([(lon0, lat0), (lon0 + dlon, lat0 + dlat)])

        try:
            hit = ray.intersection(g)
        except Exception:
            radii.append(0.0)
            continue

        if hit.is_empty:
            radii.append(0.0)
            continue

        # The far end of the intersection is the front along this bearing.
        far = _farthest_point(hit, lon0, lat0)
        if far is None:
            radii.append(0.0)
            continue

        radii.append(
            math.hypot((far[0] - lon0) * kx, (far[1] - lat0) * ky)
        )

    if not any(r > 0 for r in radii):
        return None

    return radii, (lat0, lon0)


def profile_pair(
    geom_a: Any, geom_b: Any
) -> tuple[list[float], list[float], tuple[float, float]] | None:
    """Profile two perimeters of the same fire about a shared anchor.

    Returns (radii_before, radii_after, anchor). The per-bearing difference is
    then a true front advance in km: positive where the fire moved outward,
    including the component that comes from the whole burn translating downwind.
    """
    anchor = anchor_of(geom_a)
    if anchor is None:
        return None
    a = radial_profile(geom_a, anchor)
    b = radial_profile(geom_b, anchor)
    if a is None or b is None:
        return None
    return a[0], b[0], anchor


def _farthest_point(geom: BaseGeometry, lon0: float, lat0: float) -> tuple[float, float] | None:
    origin = Point(lon0, lat0)
    best = None
    best_d = -1.0
    for part in getattr(geom, "geoms", [geom]):
        coords = list(getattr(part, "coords", []))
        if not coords and hasattr(part, "exterior"):
            coords = list(part.exterior.coords)
        for x, y in coords:
            d = origin.distance(Point(x, y))
            if d > best_d:
                best_d, best = d, (x, y)
    return best


def polygon_from_profile(
    radii: Iterable[float], centre: tuple[float, float]
) -> dict[str, Any]:
    """Rebuild a GeoJSON polygon from a radial profile. Inverse of the above."""
    lat0, lon0 = centre
    kx = _km_per_degree_lon(lat0) or 1e-9
    ky = _KM_PER_DEGREE_LAT

    ring: list[list[float]] = []
    for b, r in zip(BEARINGS, radii):
        east, north = bearing_unit(b)
        ring.append([lon0 + (east * r) / kx, lat0 + (north * r) / ky])
    ring.append(ring[0])

    return {"type": "Polygon", "coordinates": [ring]}


def wind_alignment(bearing_deg: float, wind_from_deg: float) -> float:
    """cos of the angle between a bearing and the direction the wind is pushing.

    Meteorological wind direction is the direction the wind blows *from*, so
    the push direction is 180 degrees opposed. Returns +1 directly downwind,
    -1 directly upwind. This single feature is what lets the model learn
    asymmetric, wind-driven growth.
    """
    push = (wind_from_deg + 180.0) % 360.0
    return math.cos(math.radians(bearing_deg - push))
