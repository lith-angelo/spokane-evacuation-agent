import { useEffect, useRef } from 'react'
import L from 'leaflet'

/**
 * The map.
 *
 * Colour comes from the same hazard scale as the rest of the app, read off the
 * CSS custom properties so there is exactly one definition of what Level 3 looks
 * like. Two pieces of Apple's motion guidance apply here: a large repositioning
 * surface should ease rather than jump (`flyTo`, not `setView`), and the view
 * must never be yanked out from under someone who has panned somewhere
 * deliberately.
 */

function token(name, fallback) {
  if (typeof window === 'undefined') return fallback
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

function palette() {
  return {
    l3: token('--map-l3', '#ff453a'),
    l2: token('--map-l2', '#ff9f0a'),
    l1: token('--map-l1', '#ffd60a'),
    clear: token('--map-clear', '#30d158'),
    unknown: token('--unknown', '#8e8e93'),
    fire: token('--map-fire', '#ff6b35'),
    hotspot: token('--map-hotspot', '#bf5af2'),
    route: token('--map-route', '#0a84ff'),
    routeDead: token('--map-route-dead', '#55607a'),
    sim: token('--map-sim', '#ff375f'),
    homeRing: token('--map-home-ring', '#ffffff'),
  }
}

// Clean, low-chroma basemaps. Raw OSM tiles carry so much of their own colour
// that a fire perimeter has to shout to be seen; these leave the hazard scale
// as the only saturated thing on screen, which is the point.
const BASEMAPS = {
  light: {
    url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
    attribution:
      '© OpenStreetMap contributors © CARTO — tiles loaded by the browser, not by the agent',
  },
  dark: {
    url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    attribution:
      '© OpenStreetMap contributors © CARTO — tiles loaded by the browser, not by the agent',
  },
}

function levelColor(level, p) {
  return { 3: p.l3, 2: p.l2, 1: p.l1, 0: p.clear }[level] || p.unknown
}

// ArcGIS returns {rings}/{paths}; GeoJSON returns coordinates. Leaflet wants
// [lat, lon]. Normalise all three here so no caller has to care.
function toRings(geometry) {
  if (!geometry) return []
  if (geometry.rings) return geometry.rings.map((r) => r.map(([x, y]) => [y, x]))
  const { type, coordinates } = geometry
  if (type === 'Polygon') return coordinates.map((r) => r.map(([x, y]) => [y, x]))
  if (type === 'MultiPolygon')
    return coordinates.flat().map((r) => r.map(([x, y]) => [y, x]))
  return []
}

function toLines(geometry) {
  if (!geometry) return []
  if (geometry.paths) return geometry.paths.map((p) => p.map(([x, y]) => [y, x]))
  const { type, coordinates } = geometry
  if (type === 'LineString') return [coordinates.map(([x, y]) => [y, x])]
  if (type === 'MultiLineString') return coordinates.map((p) => p.map(([x, y]) => [y, x]))
  return []
}

const prefersReducedMotion = () =>
  typeof window !== 'undefined' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches

export default function MapPanel({ state, theme = 'light' }) {
  const elRef = useRef(null)
  const mapRef = useRef(null)
  const layerRef = useRef(null)
  const fittedRef = useRef(false)
  const userMovedRef = useRef(false)
  const tileRef = useRef(null)

  useEffect(() => {
    if (mapRef.current) return

    const map = L.map(elRef.current, {
      zoomControl: true,
      attributionControl: true,
      // Momentum on pan, so the map behaves like a physical surface.
      inertia: true,
      inertiaDeceleration: 2400,
      zoomSnap: 0.25,
    })
    map.setView([47.69, -117.44], 11)


    // Once someone has driven the map themselves, stop repositioning it for
    // them. Taking the view back would override a deliberate choice.
    map.on('dragstart zoomstart', () => {
      userMovedRef.current = true
    })

    mapRef.current = map
    layerRef.current = L.layerGroup().addTo(map)

    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const base = BASEMAPS[theme] || BASEMAPS.light

    const layer = L.tileLayer(base.url, {
      maxZoom: 19,
      attribution: base.attribution,
      // Keep the outgoing tiles until the new ones have painted, so the flip
      // does not flash the empty background.
      className: 'basemap',
    })
    layer.addTo(map)

    const previous = tileRef.current
    tileRef.current = layer
    if (previous) {
      layer.once('load', () => map.removeLayer(previous))
      // If the network stalls, do not leave two basemaps stacked forever.
      setTimeout(() => map.hasLayer(previous) && map.removeLayer(previous), 2000)
    }
  }, [theme])

  useEffect(() => {
    const map = mapRef.current
    const group = layerRef.current
    if (!map || !group || !state) return

    const p = palette()
    group.clearLayers()
    const bounds = []

    // Zones sit underneath everything: they are context, not the subject.
    for (const z of state.zones || []) {
      const rings = toRings(z.record?.geometry)
      if (!rings.length) continue
      const color = levelColor(z.level, p)
      const poly = L.polygon(rings, {
        color,
        weight: 1.25,
        opacity: 0.85,
        fillColor: color,
        fillOpacity: z.level === 3 ? 0.22 : z.level === 2 ? 0.14 : 0.08,
      })
      poly.bindPopup(
        `<b>${z.level_label || 'Evacuation zone'}</b><br>${z.boundary_desc || ''}` +
          (z.public_message ? `<br><br>${z.public_message}` : '')
      )
      poly.addTo(group)
      bounds.push(poly.getBounds())
    }

    // Fire perimeters. A dashed edge means the polygon is past its freshness
    // window — the same signal the sources panel gives, in the same place the
    // eye already is.
    for (const inc of state.incidents || []) {
      const rings = toRings(inc.perimeter)
      if (!rings.length) continue
      const poly = L.polygon(rings, {
        color: p.fire,
        weight: 2,
        fillColor: p.fire,
        fillOpacity: 0.3,
        dashArray: inc.record?.stale ? '6,4' : undefined,
      })
      poly.bindPopup(
        `<b>${inc.name}</b><br>` +
          (inc.acres ? `${Math.round(inc.acres).toLocaleString()} acres` : 'size not reported') +
          (inc.containment_pct != null ? ` · ${inc.containment_pct}% contained` : '') +
          `<br><span style="opacity:.7">${inc.record?.stale ? '⚠ stale — ' : ''}${
            inc.record?.source_id || ''
          }</span>`
      )
      poly.addTo(group)
      bounds.push(poly.getBounds())
    }

    // Satellite thermal detections are points, never inferred perimeters. A
    // distinct colour and circular marker keeps that authority difference
    // visible instead of making a detection look like a mapped fire boundary.
    for (const hotspot of state.fire_hotspots || []) {
      const marker = L.circleMarker([hotspot.lat, hotspot.lon], {
        radius: hotspot.record?.stale ? 4 : 6,
        color: p.hotspot,
        weight: 2,
        fillColor: p.hotspot,
        fillOpacity: hotspot.record?.stale ? 0.25 : 0.75,
        dashArray: hotspot.record?.stale ? '3,3' : undefined,
      })
      marker.bindPopup(
        `<b>Satellite thermal detection</b><br>` +
          `${hotspot.instrument || 'sensor'} · ${hotspot.satellite || 'satellite'}` +
          (hotspot.fire_radiative_power_mw != null
            ? `<br>${hotspot.fire_radiative_power_mw.toFixed(1)} MW fire radiative power`
            : '') +
          `<br><span style="opacity:.7">${hotspot.record?.stale ? '⚠ stale — ' : ''}` +
          `FIRMS point evidence, not a perimeter</span>`
      )
      marker.addTo(group)
    }

    for (const c of state.closures || []) {
      for (const line of toLines(c.geometry)) {
        if (line.length < 2) continue
        L.polyline(line, {
          color: c.simulated ? p.sim : p.l3,
          weight: 5,
          opacity: 0.95,
          dashArray: '10,6',
          lineCap: 'round',
        })
          .bindPopup(
            `<b>${c.simulated ? 'SIMULATED — ' : ''}${c.road || 'Closure'}</b><br>${
              c.description
            }`
          )
          .addTo(group)
      }
    }

    // Rejected first so the approved route draws above it.
    for (const r of state.rejected_routes || []) {
      for (const line of toLines(r.geometry)) {
        L.polyline(line, {
          color: p.routeDead,
          weight: 3,
          opacity: 0.5,
          dashArray: '4,7',
        })
          .bindPopup(`<b>${r.route_id} — REJECTED</b><br>${r.rejection_reason || ''}`)
          .addTo(group)
      }
    }

    const current = state.current_route
    for (const r of state.approved_routes || []) {
      const isCurrent = current && r.route_id === current.route_id
      for (const line of toLines(r.geometry)) {
        // A wide, low-opacity casing under the selected route reads as depth
        // and keeps it legible over both dark parkland and pale streets.
        if (isCurrent) {
          L.polyline(line, {
            color: p.route,
            weight: 12,
            opacity: 0.22,
            lineCap: 'round',
          }).addTo(group)
        }
        const pl = L.polyline(line, {
          color: isCurrent ? p.route : '#2c6c9e',
          weight: isCurrent ? 5 : 3,
          opacity: isCurrent ? 1 : 0.5,
          lineCap: 'round',
        })
        pl.bindPopup(
          `<b>${r.route_id}${isCurrent ? ' — SELECTED' : ' — approved alternative'}</b><br>` +
            `${r.distance_km} km · ${Math.round(r.eta_min)} min` +
            (r.hazard_margin_km != null
              ? `<br>${r.hazard_margin_km.toFixed(1)} km from nearest perimeter`
              : '')
        )
        pl.addTo(group)
        if (isCurrent) bounds.push(pl.getBounds())
      }
    }

    for (const s of state.shelters || []) {
      const chosen = state.destination && s.shelter_id === state.destination.shelter_id
      const rejected = (state.rejected_shelters || []).find(
        (x) => x.shelter.shelter_id === s.shelter_id
      )
      const color = chosen ? p.clear : rejected ? p.l3 : p.unknown
      const m = L.circleMarker([s.lat, s.lon], {
        radius: chosen ? 9 : 6,
        color,
        weight: 2,
        fillColor: color,
        fillOpacity: chosen ? 0.9 : 0.3,
      })
      m.bindPopup(
        `<b>${s.name}</b><br>${s.address || ''}<br><span style="opacity:.75">` +
          (chosen
            ? '✓ meets every requirement'
            : rejected
              ? '✗ missing: ' + rejected.unmet.join(', ')
              : 'candidate') +
          '</span>'
      )
      m.addTo(group)
      if (chosen) bounds.push(L.latLngBounds([s.lat, s.lon], [s.lat, s.lon]))
    }

    if (state.place) {
      const home = L.circleMarker([state.place.lat, state.place.lon], {
        radius: 7,
        color: p.homeRing,
        weight: 3,
        fillColor: p.route,
        fillOpacity: 1,
      })
      home.bindPopup(`<b>You are here</b><br>${state.place.label || ''}`)
      home.addTo(group)
      bounds.push(
        L.latLngBounds([state.place.lat, state.place.lon], [state.place.lat, state.place.lon])
      )
    }

    if (bounds.length && !fittedRef.current && !userMovedRef.current) {
      const all = bounds.reduce((acc, b) => (acc ? acc.extend(b) : b), null)
      if (all) {
        // Ease into position instead of teleporting: a large surface that jumps
        // costs the viewer their bearings.
        const opts = {
          // Leave room for the drawer along the bottom edge.
          paddingTopLeft: [56, 56],
          paddingBottomRight: [56, 260],
        }
        if (prefersReducedMotion()) {
          map.fitBounds(all, opts)
        } else {
          map.flyToBounds(all, { ...opts, duration: 0.8, easeLinearity: 0.25 })
        }
      }
      fittedRef.current = true
    }
  }, [state, theme])

  return (
    <>
      <div className="map" ref={elRef} />
      <div className="map-card legend" aria-label="Map legend">
        {[
          ['box', 'var(--l3)', 'Level 3 zone'],
          ['box', 'var(--l2)', 'Level 2 zone'],
          ['box', 'var(--l1)', 'Level 1 zone'],
          ['box', 'var(--fire)', 'Fire perimeter'],
          ['dot', 'var(--hotspot)', 'FIRMS thermal detection'],
          ['line', 'var(--route)', 'Selected route'],
          ['line', 'var(--route-dead)', 'Rejected route'],
          ['line', 'var(--sim)', 'Simulated closure'],
        ].map(([shape, color, label]) => (
          <div className="legend-row" key={label}>
            <span
              className={`sw ${shape === 'box' ? 'box' : shape === 'dot' ? 'dot' : ''}`}
              style={{ background: color }}
            />
            {label}
          </div>
        ))}
      </div>
    </>
  )
}
