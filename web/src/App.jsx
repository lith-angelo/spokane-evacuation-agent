import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import MapPanel from './MapPanel.jsx'
import Panel from './Panel.jsx'
import ActivityDrawer from './ActivityDrawer.jsx'
import { SPRING } from './motion.js'
import { useTheme } from './theme.js'

const PRESET = {
  query: 'Rifle Club Road, Spokane County',
  needs: { pets: true, mobility: true, medical: false, service_animal: false, people: 2 },
  approved_contacts: ['+1-509-555-0142'],
}

// Direct, specific labels beat safe generic ones — these name the constraint
// the shelter filter actually applies, not a vague category.
const NEED_LABELS = {
  pets: 'Pets',
  service_animal: 'Service animal',
  mobility: 'Mobility / wheelchair',
  medical: 'Medical needs',
}

const SOURCE_META = {
  MAPBOX: { label: 'Location found', provider: 'Mapbox' },
  NOMINATIM: { label: 'Location found', provider: 'OpenStreetMap Nominatim' },
  WFIGS: { label: 'Official fire incidents', provider: 'NIFC WFIGS' },
  FIRMS: { label: 'Satellite heat detections', provider: 'NASA FIRMS' },
  SREC: { label: 'Local emergency information', provider: 'Spokane Regional Emergency Communications' },
  WSDOT: { label: 'State road alerts', provider: 'Washington State DOT' },
  WSDOT_EOC: { label: 'Emergency traffic cross-check', provider: 'WSDOT EOC' },
  OSRM: { label: 'Route calculation', provider: 'OSRM' },
  OPENAQ: { label: 'Air quality', provider: 'OpenAQ' },
  FIRECAM: { label: 'Fire camera', provider: 'ALERTWildfire' },
}

const OUTCOME_LABELS = {
  OK: 'LIVE',
  REPLAY: 'DEMO DATA',
  POLICY_DENIED: 'BLOCKED',
  UPSTREAM_ERROR: 'UNAVAILABLE',
  SANDBOX_UNAVAILABLE: 'SANDBOX OFFLINE',
}

function sourceSummary(source) {
  const count = Number(source.record_count || 0)
  const stale = Number(source.stale_count || 0)
  const fresh = Math.max(0, count - stale)

  if (source.outcome === 'POLICY_DENIED') {
    return 'OpenShell blocked this source because it is outside the approved network policy.'
  }
  if (source.outcome === 'SANDBOX_UNAVAILABLE') {
    return 'The protected network boundary is unavailable, so this source was not checked.'
  }
  if (source.outcome === 'UPSTREAM_ERROR') {
    if (source.source_id === 'WSDOT_EOC') {
      return 'The secondary emergency-traffic check failed. Primary state road alerts may still be available.'
    }
    return 'The provider could not be reached, so its information is currently unavailable.'
  }

  switch (source.source_id) {
    case 'MAPBOX':
    case 'NOMINATIM':
      return count ? 'Your address was matched to a map location.' : 'Your address could not be matched.'
    case 'WFIGS':
      if (!count) return 'No official fire incident records were found nearby.'
      if (!fresh) return `${count} nearby fire incident records were found, but all are too old to treat as current.`
      return `${fresh} current fire incident ${fresh === 1 ? 'record was' : 'records were'} found nearby${stale ? `; ${stale} older ${stale === 1 ? 'record is' : 'records are'} shown for context` : ''}.`
    case 'FIRMS':
      if (!fresh) return 'No recent satellite heat detections were found nearby.'
      return `${fresh} recent satellite heat ${fresh === 1 ? 'detection was' : 'detections were'} found nearby.`
    case 'SREC':
      if (!count) return 'No matching local evacuation, shelter, or emergency-facility records were returned.'
      return `${count} local emergency records were checked for evacuation zones and suitable destinations.`
    case 'WSDOT':
      if (!count) return 'No matching state road alerts or closures were found nearby.'
      return `${count} state road ${count === 1 ? 'alert was' : 'alerts were'} checked for closures affecting the route.`
    case 'WSDOT_EOC':
      if (!count) return 'No additional emergency traffic events were found.'
      return `${count} emergency traffic ${count === 1 ? 'event was' : 'events were'} used as a secondary check.`
    case 'OSRM':
      if (!count) return 'No road route could be calculated.'
      return `${count} road-route ${count === 1 ? 'option was' : 'options were'} calculated.`
    case 'OPENAQ':
      if (!fresh) return 'No nearby PM2.5 reading was fresh enough to use.'
      return `${fresh} current PM2.5 ${fresh === 1 ? 'reading was' : 'readings were'} available${stale ? `; ${stale} older ${stale === 1 ? 'reading was' : 'readings were'} ignored` : ''}.`
    default:
      return count
        ? `${count} matching ${count === 1 ? 'record was' : 'records were'} returned.`
        : 'The source was checked and returned no matching records.'
  }
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json()
}

function levelClass(level) {
  return { 3: 'l3', 2: 'l2', 1: 'l1', 0: 'l0' }[level] || 'unknown'
}

export default function App() {
  const { mode, resolved, cycle } = useTheme()
  const [health, setHealth] = useState(null)
  const [query, setQuery] = useState(PRESET.query)
  const [locationResolved, setLocationResolved] = useState(true)
  const [suggestions, setSuggestions] = useState([])
  const [geocodeStatus, setGeocodeStatus] = useState('')
  const skipGeocode = useRef(false)
  const [needs, setNeeds] = useState(PRESET.needs)
  const [state, setState] = useState(null)
  const [busy, setBusy] = useState(false)
  const [busyLabel, setBusyLabel] = useState('')
  const [error, setError] = useState(null)
  const [monitoring, setMonitoring] = useState(false)
  const esRef = useRef(null)

  useEffect(() => {
    api('/api/health').then(setHealth).catch(() => setHealth(null))
  }, [])

  useEffect(() => {
    if (skipGeocode.current) {
      skipGeocode.current = false
      return undefined
    }
    const address = query.trim()
    if (address.length < 3 || locationResolved) {
      setSuggestions([])
      setGeocodeStatus('')
      return undefined
    }

    let active = true
    setGeocodeStatus('Searching…')
    const timer = window.setTimeout(() => {
      api(`/api/geocode?q=${encodeURIComponent(address)}`)
        .then((data) => {
          if (!active) return
          const results = data.results || []
          setSuggestions(results)
          setGeocodeStatus(results.length ? '' : 'No Spokane-area address found')
        })
        .catch(() => {
          if (!active) return
          setSuggestions([])
          setGeocodeStatus('Address search unavailable')
        })
    }, 500)

    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [query, locationResolved])

  const chooseLocation = (place) => {
    skipGeocode.current = true
    setQuery(place.label)
    setLocationResolved(true)
    setSuggestions([])
    setGeocodeStatus('')
    setState(null)
  }

  useEffect(() => {
    if (!state?.session_id) return
    esRef.current?.close()
    const es = new EventSource(`/api/stream/${state.session_id}`)
    es.onmessage = (evt) => {
      const msg = JSON.parse(evt.data)
      if (msg.type === 'step') {
        setState((prev) => (prev ? { ...prev, steps: [...prev.steps, msg.step] } : prev))
      } else if ((msg.type === 'monitor' || msg.type === 'snapshot') && msg.state) {
        setState(msg.state)
      }
    }
    es.onerror = () => {}
    esRef.current = es
    return () => es.close()
  }, [state?.session_id])

  const act = useCallback(async (label, fn) => {
    setBusy(true)
    setBusyLabel(label)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
      setBusyLabel('')
    }
  }, [])

  const run = () =>
    act('Planning', async () => {
      setSuggestions([])
      const result = await api('/api/plan', {
        method: 'POST',
        body: JSON.stringify({
          query,
          needs,
          approved_contacts: PRESET.approved_contacts,
          session_id: state?.session_id,
        }),
      })
      setState(result)
    })

  const ask = (label, message) =>
    act(label, async () => {
      const r = await api(`/api/session/${state.session_id}/message`, {
        method: 'POST',
        body: JSON.stringify({ message }),
      })
      setState(r)
    })

  const triggerClosure = () =>
    act('Publishing closure', async () => {
      await api('/api/demo/trigger-closure', { method: 'POST' })
      const r = await api(`/api/session/${state.session_id}/monitor/check`, {
        method: 'POST',
      })
      setState(r.state)
    })

  const askUnapproved = () =>
    act('Sending', async () => {
      const r = await api(`/api/session/${state.session_id}/notify`, {
        method: 'POST',
        body: JSON.stringify({ message: 'random-person@example.com' }),
      })
      setState(r.state)
    })

  const toggleMonitor = () =>
    act(monitoring ? 'Stopping' : 'Starting', async () => {
      await api(
        `/api/session/${state.session_id}/monitor/${monitoring ? 'stop' : 'start'}`,
        { method: 'POST' }
      )
      setMonitoring(!monitoring)
    })

  const verdict = state?.verdict
  const steps = state?.steps || []
  const firstSimIndex = useMemo(() => steps.findIndex((s) => s.simulated), [steps])

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <strong>Always-On Wildfire Evacuation Agent</strong>
          <span>Spokane County</span>
        </div>

        {health && (
          <>
            <span className={`chip ${health.replay ? 'warn' : 'good'}`}>
              <span className="dot" />
              {health.replay ? 'REPLAY' : 'LIVE'}
              {health.replay && health.scenario ? ` · ${health.scenario.name}` : ''}
            </span>
            <span className={`chip ${health.policy_enforced ? 'good' : 'bad'}`}>
              <span className="dot" />
              OpenShell {health.policy_enforced ? 'enforcing' : 'NOT enforcing'}
            </span>
            <span className={`chip ${health.inference.ok ? 'good' : 'bad'}`}>
              <span className="dot" />
              {health.inference.model.split('/').pop()}
            </span>
          </>
        )}

        <div className="spacer" />

        <button
          className="btn sm icon"
          onClick={cycle}
          title={`Appearance: ${mode}. Click to change.`}
          aria-label={`Appearance: ${mode}. Click to change.`}
        >
          {mode === 'system' ? '◑' : mode === 'light' ? '☀' : '☾'}
        </button>

        <AnimatePresence>
          {state && (
            <motion.div
              initial={{ opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.96 }}
              transition={SPRING}
              style={{ display: 'flex', gap: 'var(--space-2)' }}
            >
              <button className="btn sm" onClick={toggleMonitor} disabled={busy}>
                {monitoring ? '■ Stop monitor' : '▶ Start monitor'}
              </button>
              <button className="btn sm destructive" onClick={triggerClosure} disabled={busy}>
                ⚡ Simulate road closure
              </button>
            </motion.div>
          )}
        </AnimatePresence>
      </header>

      <div className="demo-safety-notice" role="note">
        <strong>Hackathon prototype.</strong> Use synthetic household and contact data only.
        No real alerts or messages are sent; this is not 911 or certified navigation.
      </div>

      <div className="main">
        <aside className="rail" aria-label="Evacuation details">
          <Panel title="Resident" className="resident-panel">
            <div className="body">
              <label className="field-label t-caption" htmlFor="loc">
                Location — landmark or address
              </label>
              <div className="location-search">
                <input
                  id="loc"
                  type="text"
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value)
                    setLocationResolved(false)
                  }}
                  onKeyDown={(e) => e.key === 'Enter' && !busy && query.trim() && run()}
                  placeholder="e.g. Rifle Club Road, Spokane County"
                  autoComplete="off"
                  aria-expanded={suggestions.length > 0}
                  aria-controls="location-suggestions"
                />
                {suggestions.length > 0 && (
                  <div id="location-suggestions" className="location-results" role="listbox">
                    {suggestions.map((place) => (
                      <button
                        type="button"
                        key={place.id}
                        role="option"
                        onClick={() => chooseLocation(place)}
                      >
                        <span>{place.label}</span>
                        <small>{place.lat.toFixed(4)}, {place.lon.toFixed(4)}</small>
                      </button>
                    ))}
                  </div>
                )}
              </div>
              {!locationResolved && geocodeStatus && (
                <div className="location-status t-caption">{geocodeStatus}</div>
              )}

              <div className="needs">
                {Object.entries(NEED_LABELS).map(([key, label]) => (
                  <label key={key} className={`need t-caption ${needs[key] ? 'on' : ''}`}>
                    <input
                      type="checkbox"
                      checked={!!needs[key]}
                      onChange={(e) => setNeeds({ ...needs, [key]: e.target.checked })}
                    />
                    {label}
                  </label>
                ))}
              </div>

              <div className="actions">
                <button className="btn primary" onClick={run} disabled={busy || !query.trim()}>
                  {busy ? (
                    <>
                      <span className="spin" /> {busyLabel}…
                    </>
                  ) : (
                    'Plan my evacuation'
                  )}
                </button>
                {state && (
                  <button
                    className="btn ghost"
                    onClick={() => ask('Asking', 'Can I go home yet?')}
                    disabled={busy}
                  >
                    Can I go home?
                  </button>
                )}
              </div>

              {error && (
                <div className="feedback error t-caption">
                  <strong>Request failed.</strong> {error}
                </div>
              )}
            </div>
          </Panel>

          <AnimatePresence mode="popLayout">
            {verdict && (
              <Panel
                key="verdict"
                title="Recommendation"
                note="decided by the safety guard, not the model"
                className={`verdict ${levelClass(verdict.level)}`}
              >
                <div className="body">
                  <p className="headline t-display">{verdict.headline}</p>
                  <p className="action t-body">{verdict.recommended_action}</p>

                  <dl className="kv">
                    {verdict.destination && (
                      <>
                        <dt>Go to</dt>
                        <dd>{verdict.destination}</dd>
                      </>
                    )}
                    {verdict.route_summary && (
                      <>
                        <dt>Route</dt>
                        <dd>{verdict.route_summary}</dd>
                      </>
                    )}
                    {state.consensus && (
                      <>
                        <dt>Sources</dt>
                        <dd>
                          {state.consensus.sources_checked.join(' + ') || 'none'} ·{' '}
                          {state.consensus.agreed ? 'agree' : 'disagree'} · confidence{' '}
                          {state.consensus.confidence}
                        </dd>
                      </>
                    )}
                    <dt>Freshness</dt>
                    <dd className="t-caption" style={{ color: 'var(--text-tertiary)' }}>
                      {verdict.freshness_summary}
                    </dd>
                  </dl>

                  {verdict.critical_warnings?.length > 0 && (
                    <div className="feedback error t-caption">
                      <ul>
                        {verdict.critical_warnings.map((w, i) => (
                          <li key={i}>{w}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {verdict.unverified?.length > 0 && (
                    <div className="feedback warning t-caption">
                      <ul>
                        {verdict.unverified.map((u, i) => (
                          <li key={i}>{u}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {verdict.narrative && (
                    <div className="narrative t-callout">
                      <span className="tag t-label">
                        Model-written summary — rendered around the verdict above
                      </span>
                      {verdict.narrative}
                    </div>
                  )}
                </div>
              </Panel>
            )}

            {state?.monitor_events?.length > 0 && (
              <Panel
                key="monitor"
                title="Autonomous actions"
                note="no user message involved"
              >
                <div className="body" style={{ display: 'grid', gap: 'var(--space-2)' }}>
                  {state.monitor_events.map((e, i) => (
                    <div key={i} className="feedback sim t-caption" style={{ margin: 0 }}>
                      {e.simulated && <strong>SIMULATED EVENT · </strong>}
                      {e.changes.join('; ')}
                      {e.replanned && (
                        <>
                          <br />
                          Route <strong>{e.previous_route}</strong> invalidated → replanned
                          to <strong>{e.new_route || 'no safe route'}</strong>
                        </>
                      )}
                      {e.notification && (
                        <>
                          <br />
                          <span style={{ color: 'var(--text-tertiary)' }}>
                            Notification prepared: {e.notification}
                          </span>
                        </>
                      )}
                    </div>
                  ))}
                </div>
              </Panel>
            )}

            {state?.shelters?.length > 0 && (
              <Panel
                key="shelters"
                title="Shelters"
                note="hard constraints applied before distance"
              >
                <div className="scroll-region">
                  {state.destination && (
                    <div className="row picked">
                      <div className="row-head">
                        <span className="name">{state.destination.name}</span>
                        <span className="verdict-tag ok">✓ SELECTED</span>
                      </div>
                      <div className="meta">
                        {state.destination.address} ·{' '}
                        {state.destination.distance_km?.toFixed(1)} km
                      </div>
                      <div className="caps">
                        {(state.destination.capabilities || []).map((c) => (
                          <span className="cap" key={c}>
                            {c}
                          </span>
                        ))}
                      </div>
                      {state.destination.air_quality?.status === 'available' && (
                        <div className="meta">
                          PM2.5 {state.destination.air_quality.max_pm25.toFixed(1)} µg/m³ · OpenAQ
                        </div>
                      )}
                    </div>
                  )}
                  {(state.rejected_shelters || []).map(({ shelter, unmet }) => (
                    <div className="row out" key={shelter.shelter_id}>
                      <div className="row-head">
                        <span className="name">{shelter.name}</span>
                        <span className="verdict-tag no">✗ REJECTED</span>
                      </div>
                      <div className="meta">
                        {shelter.distance_km?.toFixed(1)} km — not eligible for this
                        evacuation
                      </div>
                      <div className="caps">
                        {unmet.map((u) => (
                          <span className="cap missing" key={u}>
                            {u.startsWith('hazard:') ? u.slice(7).trim() : `missing ${u}`}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </Panel>
            )}

            {(state?.approved_routes?.length > 0 || state?.rejected_routes?.length > 0) && (
              <Panel key="routes" title="Route candidates" note="validated, not just generated">
                <div>
                  {(state.approved_routes || []).map((r) => (
                    <div
                      className={`row ${
                        state.current_route?.route_id === r.route_id ? 'picked' : ''
                      }`}
                      key={r.route_id}
                    >
                      <div className="row-head">
                        <span className="name">{r.route_id}</span>
                        <span className="verdict-tag ok">
                          {state.current_route?.route_id === r.route_id
                            ? '✓ SELECTED'
                            : '✓ approved'}
                        </span>
                      </div>
                      <div className="meta">
                        {r.distance_km} km · {Math.round(r.eta_min)} min
                        {r.hazard_margin_km != null &&
                          ` · ${r.hazard_margin_km.toFixed(1)} km hazard margin`}
                        {r.air_quality?.status === 'available' &&
                          ` · PM2.5 ${r.air_quality.max_pm25.toFixed(1)} µg/m³`}
                      </div>
                      {r.air_quality_warning && (
                        <div className="meta" style={{ color: 'var(--warning)' }}>
                          {r.air_quality_warning}
                        </div>
                      )}
                    </div>
                  ))}
                  {(state.rejected_routes || []).map((r) => (
                    <div className="row out" key={r.route_id}>
                      <div className="row-head">
                        <span className="name">{r.route_id}</span>
                        <span className="verdict-tag no">✗ REJECTED</span>
                      </div>
                      <div className="meta">
                        {r.distance_km} km · {Math.round(r.eta_min)} min —{' '}
                        {r.rejection_reason}
                      </div>
                      {r.air_quality?.status === 'available' && (
                        <div className="meta">
                          PM2.5 {r.air_quality.max_pm25.toFixed(1)} µg/m³ · OpenAQ
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </Panel>
            )}

            {state?.fire_hotspots?.length > 0 && (
              <Panel
                key="firms"
                title="Satellite detections"
                note="FIRMS points — not incidents or perimeters"
              >
                <div>
                  {state.fire_hotspots.slice(0, 6).map((hotspot) => (
                    <div className="row" key={hotspot.hotspot_id}>
                      <div className="row-head">
                        <span className="name">Thermal detection</span>
                        <span className={`outcome ${hotspot.record?.stale ? 'o-UPSTREAM_ERROR' : 'o-OK'}`}>
                          {hotspot.record?.stale ? 'STALE' : 'FRESH'}
                        </span>
                      </div>
                      <div className="meta">
                        {hotspot.distance_km?.toFixed(1)} km away · {hotspot.instrument || 'sensor'}{' '}
                        {hotspot.satellite || ''}
                        {hotspot.fire_radiative_power_mw != null &&
                          ` · ${hotspot.fire_radiative_power_mw.toFixed(1)} MW`}
                        <br />FIRMS, observed {new Date(hotspot.acquired_at).toLocaleString()}
                      </div>
                    </div>
                  ))}
                </div>
              </Panel>
            )}

            {state?.blocked?.length > 0 && (
              <Panel
                key="blocked"
                title="Blocked actions"
                note="refused outside the agent"
                tone="var(--blocked)"
              >
                <div>
                  {state.blocked.map((b, i) => (
                    <div className="row" key={i}>
                      <div className="row-head">
                        <span className="name" style={{ color: 'var(--blocked)' }}>
                          {b.tool}
                        </span>
                        <span className="verdict-tag blocked">⛔ BLOCKED</span>
                      </div>
                      <div className="meta">
                        {b.host}
                        {b.path} · policy <code>{b.policy}</code> · layer{' '}
                        <code>{b.layer}</code>
                        <br />
                        {b.detail}
                      </div>
                    </div>
                  ))}
                </div>
              </Panel>
            )}

            {state?.sources?.length > 0 && (
              <Panel key="sources" title="Live evidence" note="what each source says in plain language">
                <div>
                  {state.sources.map((s) => {
                    const meta = SOURCE_META[s.source_id] || {
                      label: s.source_id,
                      provider: s.source_id,
                    }
                    return (
                      <div className="src" key={s.source_id}>
                        <div className="src-head">
                          <span className="id">{meta.label}</span>
                          <span className={`outcome o-${s.outcome}`}>
                            {OUTCOME_LABELS[s.outcome] || s.outcome}
                          </span>
                        </div>
                        <div className="summary">{sourceSummary(s)}</div>
                        <div className="detail">
                          {meta.provider} · {s.record_count} {s.record_count === 1 ? 'record' : 'records'}
                          {s.stale_count > 0 && ` · ${s.stale_count} stale`}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </Panel>
            )}

            {state && (
              <Panel key="containment" title="Boundary demonstrations">
                <div className="body" style={{ display: 'grid', gap: 'var(--space-2)' }}>
                  <button
                    className="btn sm block"
                    onClick={() =>
                      ask(
                        'Asking',
                        'Check the fire camera to visually confirm where the fire front is right now.'
                      )
                    }
                    disabled={busy}
                  >
                    Ask the agent to use the fire camera
                  </button>
                  <button className="btn sm block" onClick={askUnapproved} disabled={busy}>
                    Send the plan to an unapproved recipient
                  </button>
                  <p className="t-caption" style={{ color: 'var(--text-quaternary)', margin: 0 }}>
                    These exercise different enforcement layers. OpenShell's L7 proxy
                    denies the real camera request because its host is absent from{' '}
                    <code>policies/spokane-evac.yaml</code>. Application code running
                    inside the sandbox denies the recipient because this session did not
                    approve it. The authorization checks are real; successful message
                    delivery is simulated in this prototype.
                  </p>
                </div>
              </Panel>
            )}
          </AnimatePresence>
        </aside>

        <div className="map-region">
          <MapPanel state={state} theme={resolved} />

          <AnimatePresence>
            {!state && (
              <motion.div
                className="map-card map-intro t-callout"
                initial={{ opacity: 0, y: -6, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -6, scale: 0.98 }}
                transition={SPRING}
              >
                <strong>Ready.</strong> Enter a location and household needs, then plan an
                evacuation. The map will show evacuation zones, fire perimeters, candidate
                routes and which of them were rejected.
                {health?.replay && health.scenario && (
                  <>
                    <br />
                    <br />
                    <strong>
                      {health.scenario.label}: {health.scenario.name}.
                    </strong>{' '}
                    {health.scenario.summary}
                  </>
                )}
              </motion.div>
            )}
          </AnimatePresence>

          <ActivityDrawer
            steps={steps}
            monitoring={monitoring}
            firstSimIndex={firstSimIndex}
          />
        </div>
      </div>
    </div>
  )
}
