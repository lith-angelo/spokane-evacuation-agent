import { useEffect, useRef } from 'react'
import L from 'leaflet'

const LEVEL_COLOR = { 3: '#ff4d4f', 2: '#ff9f2e', 1: '#ffd83d', 0: '#35d07f', '-1': '#8b97ad' }

// ArcGIS hands back {rings:[...]}; GeoJSON uses coordinates. Normalise both to
// the [[lat, lon], ...] rings Leaflet wants.
function toLatLngRings(geometry) {
  if (!geometry) return []
  if (geometry.rings) {
    return geometry.rings.map((ring) => ring.map(([lon, lat]) => [lat, lon]))
  }
  const { type, coordinates } = geometry
  if (type === 'Polygon') return coordinates.map((r) => r.map(([lon, lat]) => [lat, lon]))
  if (type === 'MultiPolygon') return coordinates.flat().map((r) => r.map(([lon, lat]) => [lat, lon]))
  return []
}

function toLatLngLines(geometry) {
  if (!geometry) return []
  if (geometry.paths) return geometry.paths.map((p) => p.map(([lon, lat]) => [lat, lon]))
  const { type, coordinates } = geometry
  if (type === 'LineString') return [coordinates.map(([lon, lat]) => [lat, lon])]
  if (type === 'MultiLineString') return coordinates.map((p) => p.map(([lon, lat]) => [lat, lon]))
  return []
}

export default function MapPanel({ state }) {
  const elRef = useRef(null)
  const mapRef = useRef(null)
  const layerRef = useRef(null)
  const fittedRef = useRef(false)

  useEffect(() => {
    if (mapRef.current) return
    const map = L.map(elRef.current, { zoomControl: true, attributionControl: true })
    map.setView([47.69, -117.44], 11)

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18,
      attribution: 'Map tiles © OpenStreetMap contributors — loaded by the browser, not by the agent',
    }).addTo(map)

    mapRef.current = map
    layerRef.current = L.layerGroup().addTo(map)
  }, [])

  useEffect(() => {
    const map = mapRef.current
    const group = layerRef.current
    if (!map || !group || !state) return

    group.clearLayers()
    const bounds = []

    // Evacuation zones, drawn under everything else.
    for (const z of state.zones || []) {
      const rings = toLatLngRings(z.record?.geometry)
      if (!rings.length) continue
      const color = LEVEL_COLOR[String(z.level)] || '#8b97ad'
      const poly = L.polygon(rings, {
        color,
        weight: 1.5,
        fillColor: color,
        fillOpacity: z.level === 3 ? 0.24 : z.level === 2 ? 0.16 : 0.1,
      })
      poly.bindPopup(
        `<b>${z.level_label || 'Zone'}</b><br>${z.boundary_desc || ''}<br>` +
          `<small>${z.public_message || ''}</small>`
      )
      poly.addTo(group)
      bounds.push(poly.getBounds())
    }

    // Fire perimeters.
    for (const inc of state.incidents || []) {
      const rings = toLatLngRings(inc.perimeter)
      if (!rings.length) continue
      const poly = L.polygon(rings, {
        color: '#ff6b3d',
        weight: 2,
        fillColor: '#ff6b3d',
        fillOpacity: 0.34,
        dashArray: inc.record?.stale ? '5,4' : undefined,
      })
      poly.bindPopup(
        `<b>${inc.name}</b><br>${inc.acres ? Math.round(inc.acres).toLocaleString() + ' acres' : 'size not reported'}` +
          `${inc.containment_pct != null ? ` · ${inc.containment_pct}% contained` : ''}` +
          `<br><small>${inc.record?.stale ? '⚠ stale — ' : ''}${inc.record?.source_id}</small>`
      )
      poly.addTo(group)
      bounds.push(poly.getBounds())
    }

    // Closures.
    for (const c of state.closures || []) {
      for (const line of toLatLngLines(c.geometry)) {
        if (line.length < 2) continue
        const pl = L.polyline(line, {
          color: c.simulated ? '#ff2ea6' : '#ff4d4f',
          weight: 5,
          opacity: 0.95,
          dashArray: '9,6',
        })
        pl.bindPopup(
          `<b>${c.simulated ? 'SIMULATED — ' : ''}${c.road || 'Closure'}</b><br>${c.description}`
        )
        pl.addTo(group)
      }
    }

    // Rejected routes first, so the approved one draws on top.
    for (const r of state.rejected_routes || []) {
      for (const line of toLatLngLines(r.geometry)) {
        L.polyline(line, { color: '#55607a', weight: 3, opacity: 0.55, dashArray: '4,6' })
          .bindPopup(`<b>${r.route_id} — REJECTED</b><br>${r.rejection_reason || ''}`)
          .addTo(group)
      }
    }

    const current = state.current_route
    for (const r of state.approved_routes || []) {
      const isCurrent = current && r.route_id === current.route_id
      for (const line of toLatLngLines(r.geometry)) {
        const pl = L.polyline(line, {
          color: isCurrent ? '#3da9fc' : '#2c6c9e',
          weight: isCurrent ? 6 : 3,
          opacity: isCurrent ? 0.95 : 0.5,
        })
        pl.bindPopup(
          `<b>${r.route_id}${isCurrent ? ' — SELECTED' : ' — approved alternative'}</b><br>` +
            `${r.distance_km} km · ${Math.round(r.eta_min)} min` +
            `${r.hazard_margin_km != null ? `<br>${r.hazard_margin_km.toFixed(1)} km from nearest perimeter` : ''}`
        )
        pl.addTo(group)
        if (isCurrent) bounds.push(pl.getBounds())
      }
    }

    // Shelters.
    for (const s of state.shelters || []) {
      const chosen = state.destination && s.shelter_id === state.destination.shelter_id
      const rejected = (state.rejected_shelters || []).find(
        (x) => x.shelter.shelter_id === s.shelter_id
      )
      const color = chosen ? '#35d07f' : rejected ? '#ff4d4f' : '#8b97ad'
      const m = L.circleMarker([s.lat, s.lon], {
        radius: chosen ? 9 : 6,
        color,
        weight: 2,
        fillColor: color,
        fillOpacity: chosen ? 0.85 : 0.35,
      })
      m.bindPopup(
        `<b>${s.name}</b><br>${s.address || ''}<br>` +
          `<small>${chosen ? '✓ meets every requirement' : rejected ? '✗ missing: ' + rejected.unmet.join(', ') : 'candidate'}</small>`
      )
      m.addTo(group)
      if (chosen) bounds.push(L.latLngBounds([[s.lat, s.lon], [s.lat, s.lon]]))
    }

    // The resident.
    if (state.place) {
      const home = L.circleMarker([state.place.lat, state.place.lon], {
        radius: 8,
        color: '#ffffff',
        weight: 3,
        fillColor: '#3da9fc',
        fillOpacity: 1,
      })
      home.bindPopup(`<b>You are here</b><br>${state.place.label || ''}`)
      home.addTo(group)
      bounds.push(L.latLngBounds([[state.place.lat, state.place.lon], [state.place.lat, state.place.lon]]))
    }

    if (bounds.length && !fittedRef.current) {
      const all = bounds.reduce((acc, b) => (acc ? acc.extend(b) : b), null)
      if (all) map.fitBounds(all, { padding: [50, 50] })
      fittedRef.current = true
    }
  }, [state])

  return (
    <>
      <div className="map" ref={elRef} />
      <div className="legend">
        <div className="row"><span className="sw box" style={{ background: '#ff4d4f' }} /> Level 3 zone</div>
        <div className="row"><span className="sw box" style={{ background: '#ff9f2e' }} /> Level 2 zone</div>
        <div className="row"><span className="sw box" style={{ background: '#ffd83d' }} /> Level 1 zone</div>
        <div className="row"><span className="sw box" style={{ background: '#ff6b3d' }} /> Fire perimeter</div>
        <div className="row"><span className="sw" style={{ background: '#3da9fc', height: 4 }} /> Selected route</div>
        <div className="row"><span className="sw" style={{ background: '#55607a' }} /> Rejected route</div>
        <div className="row"><span className="sw" style={{ background: '#ff2ea6' }} /> Simulated closure</div>
      </div>
    </>
  )
}
