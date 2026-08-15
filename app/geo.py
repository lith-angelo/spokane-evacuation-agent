"""Geometry. Pure functions over GeoJSON-ish dicts and shapely.

No network, no model, no I/O. Everything the safety layer needs to decide
whether a point is inside a zone or a route crosses a hazard lives here, so that
those decisions are testable in isolation.

Coordinates are (lon, lat) in WGS84 throughout, matching GeoJSON. Distances are
kilometres. Buffers are computed in a local metric approximation rather than in
degrees, because a degree of longitude at Spokane's latitude is ~0.67 of a
degree of latitude and a naive degree buffer would be lopsided by a third.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from shapely.geometry import LineString, Point, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _km_per_degree_lon(lat: float) -> float:
    return 111.320 * math.cos(math.radians(lat))


_KM_PER_DEGREE_LAT = 110.574


def to_geometry(obj: Any) -> BaseGeometry | None:
    """Accept GeoJSON, an ArcGIS geometry, or a shapely object.

    Returns None rather than raising: a geometry we cannot read is a coverage
    gap for the caller to report, not a crash.
    """
    if obj is None:
        return None
    if isinstance(obj, BaseGeometry):
        return obj if not obj.is_empty else None
    if not isinstance(obj, dict):
        return None

    # ArcGIS polygon / polyline / point
    if "rings" in obj:
        obj = {"type": "MultiPolygon", "coordinates": [[r] for r in obj["rings"]]}
    elif "paths" in obj:
        obj = {"type": "MultiLineString", "coordinates": obj["paths"]}
    elif "x" in obj and "y" in obj:
        if obj.get("x") is None or obj.get("y") is None:
            return None
        obj = {"type": "Point", "coordinates": [obj["x"], obj["y"]]}

    try:
        geom = shape(obj)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    if geom.is_empty:
        return None
    if not geom.is_valid:
        geom = geom.buffer(0)  # close self-intersecting rings
    return geom if not geom.is_empty else None


def as_geojson(geom: BaseGeometry | None) -> dict[str, Any] | None:
    return mapping(geom) if geom is not None else None


def point_in(lat: float, lon: float, geom: Any) -> bool:
    g = to_geometry(geom)
    return bool(g is not None and g.covers(Point(lon, lat)))


def buffer_km(geom: Any, km: float) -> BaseGeometry | None:
    """Buffer by an approximately metric distance.

    Projects degrees to a local equirectangular plane centred on the geometry,
    buffers there, and projects back. Good to well under a percent at Spokane's
    latitude over the few-kilometre buffers we use.
    """
    g = to_geometry(geom)
    if g is None:
        return None
    if km <= 0:
        return g

    lat0 = g.centroid.y
    kx = _km_per_degree_lon(lat0) or 1e-9
    ky = _KM_PER_DEGREE_LAT

    fwd = transform(lambda x, y, z=None: (x * kx, y * ky), g)
    back = transform(lambda x, y, z=None: (x / kx, y / ky), fwd.buffer(km))
    return back


def distance_km(a: Any, b: Any) -> float | None:
    """Shortest distance between two geometries, in kilometres."""
    ga, gb = to_geometry(a), to_geometry(b)
    if ga is None or gb is None:
        return None
    if ga.intersects(gb):
        return 0.0

    p, q = _nearest_pair(ga, gb)
    return haversine_km(p.y, p.x, q.y, q.x)


def _nearest_pair(ga: BaseGeometry, gb: BaseGeometry) -> tuple[Point, Point]:
    from shapely.ops import nearest_points

    p, q = nearest_points(ga, gb)
    return p, q


def point_distance_km(lat: float, lon: float, geom: Any) -> float | None:
    return distance_km(Point(lon, lat), geom)


def line_from_coords(coords: Iterable[Iterable[float]]) -> LineString | None:
    pts = [(float(c[0]), float(c[1])) for c in coords]
    if len(pts) < 2:
        return None
    return LineString(pts)


def intersects(a: Any, b: Any, *, buffer: float = 0.0) -> bool:
    """Does `a` touch `b`, optionally after growing `b` by `buffer` km?"""
    ga = to_geometry(a)
    gb = buffer_km(b, buffer) if buffer else to_geometry(b)
    return bool(ga is not None and gb is not None and ga.intersects(gb))


def clearance_km(route: Any, hazards: Iterable[Any]) -> float | None:
    """Smallest distance from the route to any hazard.

    None when there are no hazards to measure against — which the caller must
    treat as "unmeasured", not as "infinitely safe".
    """
    g = to_geometry(route)
    if g is None:
        return None
    best: float | None = None
    for h in hazards:
        d = distance_km(g, h)
        if d is None:
            continue
        best = d if best is None else min(best, d)
    return best


def bbox_around(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """(xmin, ymin, xmax, ymax) envelope for an ArcGIS spatial query."""
    dlat = radius_km / _KM_PER_DEGREE_LAT
    dlon = radius_km / (_km_per_degree_lon(lat) or 1e-9)
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def encode_polyline(points: Iterable[tuple[float, float]], precision: int = 5) -> str:
    """Google-encoded polyline from (lat, lon) pairs.

    Needed because the sandbox's L7 proxy truncates a URL path at `;`, which is
    OSRM's coordinate separator — see docs/SOURCES.md. OSRM's `polyline(...)`
    input carries every waypoint with no separator at all.
    """
    factor = 10**precision
    out: list[str] = []
    prev_lat = prev_lon = 0

    def chunk(value: int) -> None:
        v = ~(value << 1) if value < 0 else (value << 1)
        while v >= 0x20:
            out.append(chr((0x20 | (v & 0x1F)) + 63))
            v >>= 5
        out.append(chr(v + 63))

    for lat, lon in points:
        ilat = int(round(lat * factor))
        ilon = int(round(lon * factor))
        chunk(ilat - prev_lat)
        chunk(ilon - prev_lon)
        prev_lat, prev_lon = ilat, ilon

    return "".join(out)


def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    """Inverse of `encode_polyline`, returning (lat, lon) pairs."""
    factor = 10**precision
    coords: list[tuple[float, float]] = []
    index = lat = lon = 0

    while index < len(encoded):
        for axis in range(2):
            shift = result = 0
            while True:
                if index >= len(encoded):
                    return coords
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if axis == 0:
                lat += delta
            else:
                lon += delta
        coords.append((lat / factor, lon / factor))

    return coords
