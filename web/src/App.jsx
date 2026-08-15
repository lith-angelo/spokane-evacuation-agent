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

      <div className="main">
        <div className="rail">
          <Panel title="Resident">
            <div className="body">
              <label className="field-label t-caption" htmlFor="loc">
                Location — landmark or address
              </label>
              <input
                id="loc"
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && !busy && query.trim() && run()}
                placeholder="e.g. Rifle Club Road, Spokane County"
              />

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
                    </div>
                  )}
                  {(state.rejected_shelters || []).map(({ shelter, unmet }) => (
                    <div className="row out" key={shelter.shelter_id}>
                      <div className="row-head">
                        <span className="name">{shelter.name}</span>
                        <span className="verdict-tag no">✗ REJECTED</span>
                      </div>
                      <div className="meta">
                        {shelter.distance_km?.toFixed(1)} km — closer, but does not meet
                        every requirement
                      </div>
                      <div className="caps">
                        {unmet.map((u) => (
                          <span className="cap missing" key={u}>
                            missing {u}
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
                      </div>
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
              <Panel key="sources" title="Sources" note="every answer carries its provenance">
                <div>
                  {state.sources.map((s) => (
                    <div className="src" key={s.source_id}>
                      <span className="id">{s.source_id}</span>
                      <span className={`outcome o-${s.outcome}`}>{s.outcome}</span>
                      <span className="detail">
                        {s.record_count} rec
                        {s.stale_count > 0 && `, ${s.stale_count} stale`}
                        {s.detail ? ` — ${s.detail}` : ''}
                      </span>
                    </div>
                  ))}
                </div>
              </Panel>
            )}

            {state && (
              <Panel key="containment" title="Containment checks">
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
                    Both are real capabilities. The first is refused by the OpenShell L7
                    proxy because its host is absent from{' '}
                    <code>policies/spokane-evac.yaml</code>; the second by the session's
                    action scope. Neither refusal is simulated by the agent.
                  </p>
                </div>
              </Panel>
            )}
          </AnimatePresence>
        </div>

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
