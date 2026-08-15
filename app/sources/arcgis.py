"""Shared ArcGIS REST query helper.

WFIGS, SREC, WSDOT and Spokane County all speak the same query grammar, so the
URL building, the envelope filter and the failure handling live in one place.

Two proxy quirks are handled here rather than at each call site (see
docs/SOURCES.md): directory-style paths need a trailing slash to match the
policy's `**`, and every request must survive being refused without raising.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.egress import EgressResult, Outcome, egress
from app.geo import bbox_around


@dataclass
class LayerQuery:
    """A single FeatureServer layer plus the fields we actually use."""

    base_url: str
    out_fields: str = "*"

    def url(self) -> str:
        return f"{self.base_url}/query"


async def query_layer(
    layer: LayerQuery,
    *,
    where: str = "1=1",
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float | None = None,
    return_geometry: bool = True,
    result_record_count: int = 200,
    out_sr: int = 4326,
    timeout: float = 30.0,
) -> tuple[list[dict[str, Any]], EgressResult]:
    """Run an ArcGIS query and return `(features, result)`.

    Never raises. On any non-OK outcome the feature list is empty and the caller
    reports the source as unavailable or blocked — an empty list must never be
    presented as "we checked and there is nothing there".
    """
    params: dict[str, Any] = {
        "where": where,
        "outFields": layer.out_fields,
        "returnGeometry": "true" if return_geometry else "false",
        "outSR": out_sr,
        "f": "json",
        "resultRecordCount": result_record_count,
    }

    if lat is not None and lon is not None and radius_km:
        xmin, ymin, xmax, ymax = bbox_around(lat, lon, radius_km)
        params["geometry"] = f"{xmin},{ymin},{xmax},{ymax}"
        params["geometryType"] = "esriGeometryEnvelope"
        params["inSR"] = 4326
        params["spatialRel"] = "esriSpatialRelIntersects"

    result = await egress.fetch(layer.url(), params=params, timeout=timeout)
    if not result.ok:
        return [], result

    data = result.json()
    if not isinstance(data, dict):
        return [], _degrade(result, "response was not JSON")

    if "error" in data:
        msg = data["error"].get("message", "ArcGIS error")
        details = "; ".join(data["error"].get("details", []) or [])
        return [], _degrade(result, f"{msg} {details}".strip())

    features = data.get("features")
    if not isinstance(features, list):
        return [], _degrade(result, "response had no feature list")

    return features, result


def _degrade(result: EgressResult, why: str) -> EgressResult:
    """An OK transport carrying an unusable payload is an upstream error.

    Keeping this distinct from OK-with-zero-features is the whole point: one
    means "the source answered and there is nothing", the other means "we do not
    know". Only the first is safe to summarise as no hazard.
    """
    result.outcome = Outcome.UPSTREAM_ERROR
    result.error = why
    return result
