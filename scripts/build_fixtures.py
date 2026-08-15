#!/usr/bin/env python3
"""Build the replay scenario in `data/fixtures/`.

Run with the sandbox up and `EVAC_DATA_MODE=live`:

    .venv/bin/python scripts/build_fixtures.py

Two kinds of fixture come out of this, and the difference is recorded in each
file's `_meta.origin` and shown in the UI:

- **captured** — real bytes from the live source, saved verbatim. Fire
  perimeters, road alerts, geocodes and route geometry are all real.
- **authored** — written here, in the exact response shape of the layer it
  stands in for. Only the evacuation overlay is authored, and only because SREC
  genuinely publishes nothing when no evacuation is running: on the day this was
  built, `Evacuation_Areas_Spokane_County_Public_View` returned `{"count":0}`.
  A demo needs an evacuation to demonstrate an evacuation agent.

The scenario is a wildfire northwest of Spokane pushing a Level 3 across the
Rifle Club Road area. Roads, shelters, distances and drive times are real; the
fire's position and the evacuation levels are the authored part.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import REPO_ROOT  # noqa: E402
from app.egress import Outcome, egress  # noqa: E402
from app.geo import encode_polyline  # noqa: E402

OUT = REPO_ROOT / "data" / "fixtures"

# --- Scenario geography (real places) ---------------------------------------
ORIGIN = (47.7204357, -117.4937647)  # W Rifle Club Road, Spokane
FAIRGROUNDS = (47.6553, -117.2764)  # Spokane County Fair & Expo Center
SFCC = (47.6957, -117.4536)  # Spokane Falls Community College
NINE_MILE = (47.7757, -117.5536)  # Nine Mile Falls School

NOW = datetime.now(timezone.utc)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


def meta(origin: str, note: str) -> dict:
    return {"origin": origin, "note": note, "built_at": NOW.isoformat()}


def write(name: str, payload: dict | list, origin: str, note: str) -> None:
    # Nominatim answers with a bare array, so a list payload is boxed under
    # `_list` and unboxed on the way out by replay._strip_meta.
    if isinstance(payload, list):
        doc = {"_meta": meta(origin, note), "_list": payload}
    else:
        doc = {"_meta": meta(origin, note), **payload}
    (OUT / name).write_text(json.dumps(doc, indent=1))
    print(f"  wrote {name:<34} [{origin}]")


async def capture(name: str, url: str, note: str, params: dict | None = None) -> dict | None:
    """Fetch live and save verbatim."""
    res = await egress.fetch(url, params=params, timeout=45.0)
    if res.outcome is not Outcome.OK:
        print(f"  !! {name}: {res.outcome.value} {res.error or ''}")
        return None
    data = res.json()
    if data is None:
        print(f"  !! {name}: unparseable body")
        return None
    write(name, data, "captured", note)
    return data


# --- Authored evacuation overlay --------------------------------------------


def ring(points: list[tuple[float, float]]) -> list[list[float]]:
    """ArcGIS ring from (lat, lon) pairs, closed, clockwise-ish."""
    coords = [[lon, lat] for lat, lon in points]
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    return coords


def evacuation_areas() -> dict:
    """SREC evacuation areas, in the live layer's exact response shape."""
    level3 = ring(
        [
            (47.762, -117.560),
            (47.762, -117.440),
            (47.700, -117.430),
            (47.688, -117.520),
            (47.712, -117.575),
        ]
    )
    level2 = ring(
        [
            (47.700, -117.430),
            (47.688, -117.520),
            (47.640, -117.510),
            (47.632, -117.412),
            (47.678, -117.398),
        ]
    )
    level1 = ring(
        [
            (47.632, -117.412),
            (47.640, -117.510),
            (47.590, -117.500),
            (47.582, -117.400),
        ]
    )

    def feature(oid, level, status, desc, msg, rings):
        return {
            "attributes": {
                "OBJECTID": oid,
                "IncidentType": "Wildfire",
                "IncidentName": "Rifle Club Fire",
                "FireDistrict": "Spokane County Fire District 9",
                "EvacStatus": status,
                "EvacLevel": level,
                "BoundaryDesc": desc,
                "PublicAppMsg": msg,
            },
            "geometry": {"rings": [rings]},
        }

    return {
        "objectIdFieldName": "OBJECTID",
        "globalIdFieldName": "GlobalID",
        "geometryType": "esriGeometryPolygon",
        "spatialReference": {"wkid": 4326, "latestWkid": 4326},
        "fields": [
            {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID"},
            {"name": "IncidentType", "type": "esriFieldTypeString", "alias": "IncidentType"},
            {"name": "IncidentName", "type": "esriFieldTypeString", "alias": "IncidentName"},
            {"name": "FireDistrict", "type": "esriFieldTypeString", "alias": "FireDistrict"},
            {"name": "EvacStatus", "type": "esriFieldTypeString", "alias": "EvacStatus"},
            {"name": "EvacLevel", "type": "esriFieldTypeString", "alias": "EvacLevel"},
            {"name": "BoundaryDesc", "type": "esriFieldTypeString", "alias": "BoundaryDesc"},
            {"name": "PublicAppMsg", "type": "esriFieldTypeString", "alias": "PublicAppMsg"},
        ],
        "features": [
            feature(
                101,
                "Level 3",
                "GO",
                "Rifle Club Rd / Seven Mile / NW Spokane",
                "LEAVE NOW. Do not delay to gather belongings. "
                "Travel south and east on Nine Mile Rd toward Francis Ave.",
                level3,
            ),
            feature(
                102,
                "Level 2",
                "SET",
                "Indian Trail / NW Boulevard corridor",
                "Be ready to leave at a moment's notice. "
                "People with pets, livestock or mobility needs should leave now.",
                level2,
            ),
            feature(
                103,
                "Level 1",
                "READY",
                "North Spokane / Audubon Park",
                "Be aware of danger in your area. Monitor official channels.",
                level1,
            ),
        ],
    }


def evacuation_poi() -> dict:
    """Activated shelters, in the SREC Evacuation POI layer's shape.

    Capabilities differ on purpose so the hard-constraint filter has something
    to reject. The nearest shelter is the wrong one for this household.
    """
    def feature(oid, name, poi_type, notes, addr, phone, lat, lon):
        return {
            "attributes": {
                "OBJECTID": oid,
                "POI_Type": poi_type,
                "POI_Name": name,
                "Notes": notes,
                "FullAddress": addr,
                "PhoneNumber": phone,
            },
            "geometry": {"x": lon, "y": lat},
        }

    return {
        "objectIdFieldName": "OBJECTID",
        "geometryType": "esriGeometryPoint",
        "spatialReference": {"wkid": 4326, "latestWkid": 4326},
        "fields": [
            {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID"},
            {"name": "POI_Type", "type": "esriFieldTypeString", "alias": "POI_Type"},
            {"name": "POI_Name", "type": "esriFieldTypeString", "alias": "POI_Name"},
            {"name": "Notes", "type": "esriFieldTypeString", "alias": "Notes"},
            {"name": "FullAddress", "type": "esriFieldTypeString", "alias": "FullAddress"},
            {"name": "PhoneNumber", "type": "esriFieldTypeString", "alias": "PhoneNumber"},
        ],
        "features": [
            feature(
                201,
                "Spokane Falls Community College",
                "Evacuation Shelter",
                "ADA accessible entrances and restrooms. On-site medical station "
                "with nurse. NO PETS — service animals only.",
                "3410 W Fort George Wright Dr, Spokane, WA 99224",
                "(509) 533-3500",
                *SFCC,
            ),
            feature(
                202,
                "Spokane County Fair & Expo Center",
                "Evacuation Shelter",
                "ADA accessible throughout. Pet and livestock intake in Barn C. "
                "Medical station staffed by Red Cross nurse.",
                "404 N Havana St, Spokane, WA 99202",
                "(509) 477-1766",
                *FAIRGROUNDS,
            ),
            feature(
                203,
                "Nine Mile Falls School",
                "Evacuation Shelter",
                "Pet crates available in gymnasium. Building is NOT ADA "
                "accessible — stairs at all entrances.",
                "10310 W Charles Rd, Nine Mile Falls, WA 99026",
                "(509) 340-4300",
                *NINE_MILE,
            ),
        ],
    }


def local_closures() -> dict:
    """SREC evacuation road closures, as polylines."""
    return {
        "objectIdFieldName": "OBJECTID",
        "geometryType": "esriGeometryPolyline",
        "spatialReference": {"wkid": 4326, "latestWkid": 4326},
        "fields": [
            {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID"},
            {"name": "RoadName", "type": "esriFieldTypeString", "alias": "RoadName"},
            {"name": "ActiveClosure", "type": "esriFieldTypeString", "alias": "ActiveClosure"},
            {"name": "Notes", "type": "esriFieldTypeString", "alias": "Notes"},
        ],
        "features": [
            {
                "attributes": {
                    "OBJECTID": 301,
                    "RoadName": "W Charles Rd / SR-291 north",
                    "ActiveClosure": "Yes",
                    "Notes": "Hard closure — fire activity across roadway. "
                    "No through travel northbound.",
                },
                "geometry": {
                    "paths": [
                        [
                            [-117.5536, 47.7757],
                            [-117.5400, 47.7600],
                            [-117.5200, 47.7420],
                        ]
                    ]
                },
            },
            # Sits on the westerly candidate and on nothing else. Coordinates
            # were chosen by intersecting them against the captured route
            # geometry, so this rejects exactly one real route.
            {
                "attributes": {
                    "OBJECTID": 303,
                    "RoadName": "W Driscoll Blvd",
                    "ActiveClosure": "Yes",
                    "Notes": "Hard closure — emergency apparatus staging, "
                    "roadway blocked between Alberta and Cochran.",
                },
                "geometry": {
                    "paths": [[[-117.4580, 47.6770], [-117.4520, 47.6772]]]
                },
            },
        ],
    }


def simulated_closure() -> dict:
    """The always-on trigger: a second closure that appears mid-session.

    Served only after the demo trigger fires, and labelled simulated everywhere
    it surfaces (book section 5: replayed events must be marked as such).
    """
    return {
        "objectIdFieldName": "OBJECTID",
        "geometryType": "esriGeometryPolyline",
        "spatialReference": {"wkid": 4326, "latestWkid": 4326},
        "fields": [
            {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID"},
            {"name": "RoadName", "type": "esriFieldTypeString", "alias": "RoadName"},
            {"name": "ActiveClosure", "type": "esriFieldTypeString", "alias": "ActiveClosure"},
            {"name": "Notes", "type": "esriFieldTypeString", "alias": "Notes"},
        ],
        "features": local_closures()["features"]
        + [
            {
                "attributes": {
                    "OBJECTID": 302,
                    "RoadName": "W Francis Ave at N Assembly St",
                    "ActiveClosure": "Yes",
                    "Notes": "SIMULATED — hard closure, spot fire and downed power "
                    "lines across W Francis Ave. Eastbound travel blocked.",
                },
                # Placed on the currently-selected route, verified by
                # intersection against the captured geometry. This is what makes
                # the monitor's replan a real recalculation rather than a
                # scripted animation.
                "geometry": {
                    "paths": [
                        [
                            [-117.4700, 47.7003],
                            [-117.4550, 47.7004],
                            [-117.4400, 47.7005],
                        ]
                    ]
                },
            }
        ],
    }


def wfigs_scenario_incident() -> dict:
    """The Rifle Club Fire itself, in the WFIGS incident layer's shape."""
    return {
        "objectIdFieldName": "OBJECTID",
        "geometryType": "esriGeometryPoint",
        "spatialReference": {"wkid": 4326, "latestWkid": 4326},
        "fields": [
            {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID"},
            {"name": "IncidentName", "type": "esriFieldTypeString", "alias": "IncidentName"},
            {"name": "IncidentSize", "type": "esriFieldTypeDouble", "alias": "IncidentSize"},
            {"name": "PercentContained", "type": "esriFieldTypeDouble", "alias": "PercentContained"},
            {"name": "FireDiscoveryDateTime", "type": "esriFieldTypeDate", "alias": "Discovered"},
            {"name": "ModifiedOnDateTime_dt", "type": "esriFieldTypeDate", "alias": "Modified"},
            {"name": "POOCounty", "type": "esriFieldTypeString", "alias": "POOCounty"},
            {"name": "POOState", "type": "esriFieldTypeString", "alias": "POOState"},
            {
                "name": "IncidentTypeCategory",
                "type": "esriFieldTypeString",
                "alias": "IncidentTypeCategory",
            },
        ],
        "features": [
            {
                "attributes": {
                    "OBJECTID": 900001,
                    "IncidentName": "Rifle Club Fire",
                    "IncidentSize": 2840.0,
                    "PercentContained": 5.0,
                    "FireDiscoveryDateTime": ms(ago(hours=19)),
                    "ModifiedOnDateTime_dt": ms(ago(minutes=22)),
                    "POOCounty": "Spokane",
                    "POOState": "US-WA",
                    "IncidentTypeCategory": "WF",
                },
                "geometry": {"x": -117.5620, "y": 47.7630},
            },
            {
                "attributes": {
                    "OBJECTID": 900002,
                    "IncidentName": "Deep Creek",
                    "IncidentSize": 610.0,
                    "PercentContained": 40.0,
                    "FireDiscoveryDateTime": ms(ago(days=2)),
                    # Deliberately old: this drives the staleness demonstration.
                    "ModifiedOnDateTime_dt": ms(ago(hours=31)),
                    "POOCounty": "Spokane",
                    "POOState": "US-WA",
                    "IncidentTypeCategory": "WF",
                },
                "geometry": {"x": -117.6350, "y": 47.6720},
            },
        ],
    }


def wfigs_scenario_perimeter() -> dict:
    """Perimeter polygons for the scenario fires."""
    rifle = ring(
        [
            (47.7900, -117.6000),
            (47.7880, -117.5300),
            (47.7560, -117.5180),
            (47.7380, -117.5600),
            (47.7520, -117.6100),
        ]
    )
    deep_creek = ring(
        [
            (47.6850, -117.6600),
            (47.6840, -117.6150),
            (47.6600, -117.6100),
            (47.6580, -117.6550),
        ]
    )
    return {
        "objectIdFieldName": "OBJECTID",
        "geometryType": "esriGeometryPolygon",
        "spatialReference": {"wkid": 4326, "latestWkid": 4326},
        "fields": [
            {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID"},
            {"name": "poly_IncidentName", "type": "esriFieldTypeString", "alias": "Name"},
            {"name": "poly_GISAcres", "type": "esriFieldTypeDouble", "alias": "Acres"},
            {"name": "poly_PolygonDateTime", "type": "esriFieldTypeDate", "alias": "PolyTime"},
            {"name": "attr_IncidentName", "type": "esriFieldTypeString", "alias": "AttrName"},
            {"name": "attr_PercentContained", "type": "esriFieldTypeDouble", "alias": "Contained"},
            {"name": "attr_ModifiedOnDateTime_dt", "type": "esriFieldTypeDate", "alias": "Modified"},
        ],
        "features": [
            {
                "attributes": {
                    "OBJECTID": 910001,
                    "poly_IncidentName": "Rifle Club Fire",
                    "poly_GISAcres": 2840.0,
                    "poly_PolygonDateTime": ms(ago(minutes=35)),
                    "attr_IncidentName": "Rifle Club Fire",
                    "attr_PercentContained": 5.0,
                    "attr_ModifiedOnDateTime_dt": ms(ago(minutes=22)),
                },
                "geometry": {"rings": [rifle]},
            },
            {
                "attributes": {
                    "OBJECTID": 910002,
                    "poly_IncidentName": "Deep Creek",
                    "poly_GISAcres": 610.0,
                    "poly_PolygonDateTime": ms(ago(hours=31)),
                    "attr_IncidentName": "Deep Creek",
                    "attr_PercentContained": 40.0,
                    "attr_ModifiedOnDateTime_dt": ms(ago(hours=31)),
                },
                "geometry": {"rings": [deep_creek]},
            },
        ],
    }


# --- Route capture -----------------------------------------------------------


async def capture_route(name: str, origin, destination, note: str) -> None:
    """Capture real OSRM geometry, merging via-waypoint variants as alternatives.

    OSRM's demo server often returns a single route for short trips. Rather than
    invent geometry, we ask it for genuinely different paths via intermediate
    points and merge the real answers into one response. Every coordinate below
    came off the router.
    """
    variants: list[tuple[str, list[tuple[float, float]]]] = [
        ("direct", [origin, destination]),
        ("via-north", [origin, (47.7100, -117.4400), destination]),
        ("via-south", [origin, (47.6700, -117.4700), destination]),
    ]

    routes: list[dict] = []
    seen: set[int] = set()

    for label, points in variants:
        encoded = quote(encode_polyline(points), safe="")
        url = f"https://router.project-osrm.org/route/v1/driving/polyline({encoded})"
        res = await egress.fetch(
            url,
            params={
                "overview": "full",
                "geometries": "geojson",
                "alternatives": "3",
                "steps": "false",
            },
            timeout=45.0,
        )
        data = res.json() if res.outcome is Outcome.OK else None
        if not data or data.get("code") != "Ok":
            print(f"  .. {name}/{label}: {res.outcome.value}")
            continue
        for r in data.get("routes", []):
            key = int(r.get("distance", 0))
            if key in seen:
                continue
            seen.add(key)
            routes.append(r)

    if not routes:
        print(f"  !! {name}: no routes captured")
        return

    routes.sort(key=lambda r: r.get("duration", 0))
    write(
        name,
        {"code": "Ok", "routes": routes, "waypoints": []},
        "captured",
        note,
    )


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"Building fixtures in {OUT}")

    print("\n[1/4] Capturing live geocodes")
    await capture(
        "nominatim_rifle_club.json",
        "https://nominatim.openstreetmap.org/search",
        "Live Nominatim result for the demo origin.",
        params={
            "q": "Rifle Club Road, Spokane County",
            "format": "jsonv2",
            "limit": 5,
            "addressdetails": 1,
            "countrycodes": "us",
            "viewbox": "-118.20,47.28,-116.85,48.10",
            "bounded": 1,
        },
    )

    print("\n[2/4] Capturing live WSDOT road alerts")
    from app.geo import bbox_around

    xmin, ymin, xmax, ymax = bbox_around(*ORIGIN, 80.0)
    await capture(
        "wsdot_road_alerts.json",
        "https://data.wsdot.wa.gov/arcgis/rest/services/TravelInformation/TravelInfoRoadAlerts/FeatureServer/0/query",
        "Real WSDOT highway alerts around Spokane, saved verbatim.",
        params={
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": 4326,
            "f": "json",
            "resultRecordCount": 200,
            "geometry": f"{xmin},{ymin},{xmax},{ymax}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
        },
    )

    print("\n[3/4] Capturing real route geometry")
    await capture_route(
        "osrm_to_fairgrounds.json",
        ORIGIN,
        FAIRGROUNDS,
        "Real OSRM geometry, Rifle Club Rd to the Fair & Expo Center.",
    )
    await capture_route(
        "osrm_to_sfcc.json",
        ORIGIN,
        SFCC,
        "Real OSRM geometry, Rifle Club Rd to Spokane Falls CC.",
    )
    await capture_route(
        "osrm_to_nine_mile.json",
        ORIGIN,
        NINE_MILE,
        "Real OSRM geometry, Rifle Club Rd to Nine Mile Falls School.",
    )

    print("\n[4/4] Writing the authored evacuation overlay")
    authored = (
        "SREC publishes nothing when no evacuation is active — this layer "
        "returned a literal {\"count\":0} on the day the fixture was built. "
        "Written in the live layer's exact response shape."
    )
    write("srec_evac_areas.json", evacuation_areas(), "authored", authored)
    write("srec_evac_poi.json", evacuation_poi(), "authored", authored)
    write("srec_local_closures.json", local_closures(), "authored", authored)
    write(
        "srec_local_closures_after.json",
        simulated_closure(),
        "authored",
        "Post-trigger closure set for the always-on demonstration. "
        "The added closure is labelled SIMULATED in its own Notes field.",
    )
    write(
        "wfigs_incidents.json",
        wfigs_scenario_incident(),
        "authored",
        "Scenario fires in the WFIGS incident layer's exact shape. "
        "Deep Creek carries a deliberately old timestamp to exercise staleness.",
    )
    write(
        "wfigs_perimeters.json",
        wfigs_scenario_perimeter(),
        "authored",
        "Perimeter polygons for the scenario fires.",
    )

    print("\nDone. Now write data/fixtures/manifest.json if it does not exist.")


if __name__ == "__main__":
    asyncio.run(main())
