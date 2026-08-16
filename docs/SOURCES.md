# Sources — verified URL shapes (M0)

Every shape below was probed **through the sandbox** with the `spokane-evac`
policy applied, unless a line says otherwise. Probed 2026-08-15.

Policy state at time of writing: version 15, preset `spokane-evac` active on
sandbox `my-assistant`.

## Egress signatures

Three outcomes are distinguishable without ambiguity. `app/egress.py` keys on
exactly these:

| Outcome | `nemoclaw` exit | `http_code` | Evidence |
|---|---|---|---|
| `OK` | 0 | 2xx | — |
| `POLICY_DENIED` (host not allowed) | 56 | 000 | stderr `curl: (56) CONNECT tunnel failed, response 403` |
| `POLICY_DENIED` (path/method not allowed) | 0 | 403 | body is JSON with `"error":"policy_denied"`, `"layer":"l7"` |

The second shape is the one DESIGN §6 did not anticipate. When the **host** is in
the policy but the **path** is not, the CONNECT tunnel succeeds and the L7 proxy
answers the request itself with a structured denial:

```json
{"binary":"/usr/bin/curl","detail":"GET /T4QMspbfLg3qTGWY/arcgis/rest/services not permitted by policy",
 "error":"policy_denied","host":"services3.arcgis.com","layer":"l7","method":"GET",
 "path":"/T4QMspbfLg3qTGWY/arcgis/rest/services","policy":"spokane_evac","port":443,
 "protocol":"rest","rule":"GET /T4QMspbfLg3qTGWY/arcgis/rest/services",
 "rule_missing":{"type":"rest_allow", ...}}
```

This is strictly better for the UI than the tunnel failure: it names the policy,
the rule that was missing, and the binary that tried. The blocked-action panel
renders these fields directly.

### Two proxy behaviors that constrain URL construction

1. **`**` does not match the empty remainder.** `/arcgis/rest/services` is denied
   while `/arcgis/rest/services/` is allowed. Always emit the trailing slash on
   directory-style ArcGIS requests.
2. **The proxy truncates a path at `;`.** RFC 3986 treats `;` as a path-parameter
   delimiter and the L7 layer strips from it onward. This breaks OSRM's native
   `lon,lat;lon,lat` coordinate separator — OSRM receives one coordinate and
   answers `{"code":"InvalidOptions","message":"Number of coordinates needs to be at least two."}`.
   Verified host-direct that the same URL succeeds, so it is the proxy, not OSRM.
   **Workaround:** use OSRM's `polyline(...)` input format, which encodes all
   waypoints with no `;`. See `app/sources/osrm.py`.

## Resolved open decisions

### DESIGN §9.1 — evacuation zones: SREC, not Genasys

**Genasys Protect is unusable.** `protect.genasys.com/api/`, `/api/zones`, and
`/api/v1/zones` all return the SPA's `<!doctype html>` shell, not JSON. There is
no documented public JSON route. Genasys was **removed from the policy** rather
than left as a standing grant to a host we cannot use — least privilege, and it
keeps the allowlist honest.

**Spokane County GIS carries no evacuation layer.** Its `EmergencyManagement`
folder holds only `AreasOfDistinction` and `PointsOfDistinction`.

**SREC is the Tier-1 source**, at AGOL org `9UdSzuxhN4jGcI9p`, and its org path
was added to the policy:

| Layer | Use |
|---|---|
| `Evacuation_Areas_Spokane_County_Public_View/FeatureServer/0` | Evacuation zones + `EvacLevel` |
| `Emergency_Response_Facility_Spokane_Co_Public_View/FeatureServer/0` | Shelters / facilities |
| `Evacuation_Support_Data_Spokane_County_Public_View/FeatureServer/0` | Supporting operational layers |

Evac-zone field list (verified): `IncidentType`, `IncidentName`, `FireDistrict`,
`EvacStatus`, `EvacLevel`, `BoundaryDesc`, `PublicAppMsg`, `GlobalID`, `OBJECTID`.

### Cross-source verification — what the second source actually is

The book (§4) requires cross-checking evacuation status across **at least two
sources** and exposing agreement or disagreement. With Genasys gone, the second
source is **not** a second SREC view: `Evacuation_Areas_..._Public_View` and
`..._Share_View` are views over one table, so agreement between them would be
manufactured, which is precisely the dishonesty the book warns against.

The real cross-check is between two independent authorities:

- **Source A — SREC evacuation zones** (Tier 1, official): the declared
  `EvacLevel` for the polygon containing the point.
- **Source B — NIFC WFIGS incident geometry** (Tier 1, official) reduced to an
  evacuation-relevant signal: distance from the point to the nearest active
  perimeter and incident. `data_class=derived`.

They can disagree in a way that matters. A perimeter 2 km from a point with no
SREC zone published is a real, reportable conflict — and under DESIGN §7.2
("unknown is not safe") the conservative reading wins and confidence drops. See
`evaluate_consensus` in `app/safety.py`.

### DESIGN §9.2 — county GIS host

`gismo.spokanecounty.org` is correct and reachable; `services.spokanegis.org` was
not needed. It is not called by the runtime, so it was removed from
the submitted runtime policy instead of retaining an unused standing grant.

### DESIGN §9.6 — air quality and live detection

Implemented as internal evidence layers. OpenAQ v3 supplies PM2.5 readings with
a two-hour freshness limit; NASA FIRMS supplies near-real-time VIIRS thermal
detections with a six-hour freshness limit. Both public APIs can run live with
free registered keys and require no funding or data partnership. The keys are
resolved by OpenShell and never committed.

Those feeds solve environmental detection, not local operational authority.
Production-quality evacuation-zone boundaries and live shelter status still
require county-by-county data availability and relationships.

## Verified endpoints

### NIFC WFIGS — `services3.arcgis.com/T4QMspbfLg3qTGWY`

612 services in the org. The two that matter:

```
/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0/query
/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query
```

Fields are **unprefixed** on the `_Current` layers (`IncidentName`, not
`attr_IncidentName` — passing the `attr_`-prefixed names yields
`'outFields' parameter is invalid`). Relevant fields: `IncidentName`,
`IncidentSize`, `PercentContained`, `FireDiscoveryDateTime`,
`ModifiedOnDateTime_dt`, `POOCounty`, `POOState`, `IncidentTypeCategory`.
Timestamps are epoch milliseconds. `maxRecordCount` is 2000.

Live sanity check on 2026-08-15 returned 25 Washington incidents including
`SINLAHEKIN` (152,682 ac, 42% contained), `Kaiser Canyon` (138,282 ac, 82%),
`LITTLE GIANT` (129,169 ac, 10%), and `BRADEEN HILL` (Stevens County, 4,417 ac,
92%) — the nearest large incident to Spokane.

### SREC — `services3.arcgis.com/9UdSzuxhN4jGcI9p`

See the table above. Same ArcGIS query grammar as WFIGS.

### WSDOT — `data.wsdot.wa.gov/arcgis/rest`

```
/services/TravelInformation/TravelInfoRoadAlerts/FeatureServer/0/query
/services/EOC_TrafficEvents/TrafficMgmtCenterEvents/FeatureServer/0/query
```

`TravelInfoRoadAlerts` is the closure/alert source. `TrafficMgmtCenterEvents` is
the EOC feed and is used as corroboration for closures.

### Nominatim — `nominatim.openstreetmap.org`

`/search`, `/reverse`, `/lookup`, `/status.php`. `status.php` returns `OK`.
Requires an identifying `User-Agent` and is limited to 1 req/s — `egress.py`
gives it a dedicated serial lane.

### OSRM — `router.project-osrm.org`

```
/route/v1/driving/polyline({encoded})?overview=full&alternatives=true&geometries=geojson
```

Polyline format is mandatory here, not a preference — see the `;` truncation
note above. `/nearest/v1/driving/{lon},{lat}` works unmodified (single
coordinate, no separator).

### OpenAQ v3 — `api.openaq.org`

```
/v3/locations?coordinates={lat},{lon}&radius={metres}&parameters_id=2
/v3/locations/{location_id}/latest
```

The API key is sent in `X-API-Key`. Only parameter id 2 (PM2.5) is normalized.
No nearby station, an unknown unit, or a reading older than two hours becomes
`unavailable`; none is converted to a zero value. Air quality enriches the
existing shelter and route tools rather than expanding the model's tool set.

### NASA FIRMS — `firms.modaps.eosdis.nasa.gov`

```
/api/area/csv/{MAP_KEY}/VIIRS_NOAA20_NRT/{west},{south},{east},{north}/1
```

FIRMS returns a bounding box, so the adapter applies a second radial-distance
filter. The MAP key is redacted from egress results and record provenance.
Each result remains a `FireHotspot` point with acquisition time, confidence,
brightness, and fire-radiative power; it is never converted to a WFIGS incident,
perimeter, or evacuation order.

### Deliberately denied — `cameras.alertwildfire.org`

ALERTWildfire is the camera network fire agencies use for visual confirmation of
fire position. The host resolves (`134.197.147.12`) and answers. It is absent
from the policy by design.

The denial was verified as a **two-way toggle**, which is what makes it evidence
rather than an assertion:

```console
# host absent from the policy
$ curl … https://cameras.alertwildfire.org/api/firecams/v0/cameras
curl: (56) CONNECT tunnel failed, response 403          → POLICY_DENIED

# same host added to the policy, re-applied (version 13)
HTTP 200                                                 → OK

# grant removed again, re-applied (version 14)
curl: (56) CONNECT tunnel failed, response 403          → POLICY_DENIED
```

An earlier revision pointed this tool at a placeholder domain. That was a
mistake: a non-existent host fails whether or not a policy exists, so the
refusal proved nothing. `app/sources/firecam.py` now calls a host that genuinely
serves, and nothing in the agent simulates the denial.
