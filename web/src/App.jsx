import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import MapPanel from './MapPanel.jsx'

const PRESET = {
  query: 'Rifle Club Road, Spokane County',
  needs: { pets: true, mobility: true, medical: false, service_animal: false, people: 2 },
  approved_contacts: ['+1-509-555-0142'],
}

const NEED_LABELS = {
  pets: 'Pets',
  service_animal: 'Service animal',
  mobility: 'Mobility / wheelchair',
  medical: 'Medical needs',
}

const KIND_TAG = {
  model: 'MODEL',
  tool: 'TOOL',
  'safety guard': 'GUARD',
  monitor: 'MONITOR',
  blocked: 'BLOCKED',
}
const KIND_CLASS = {
  model: 'k-model',
  tool: 'k-tool',
  'safety guard': 'k-guard',
  monitor: 'k-monitor',
  blocked: 'k-blocked',
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
  if (level === 3) return 'l3'
  if (level === 2) return 'l2'
  if (level === 1) return 'l1'
  if (level === 0) return 'l0'
  return 'unknown'
}

function timeOf(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString([], { hour12: false })
}

export default function App() {
  const [health, setHealth] = useState(null)
  const [query, setQuery] = useState(PRESET.query)
  const [needs, setNeeds] = useState(PRESET.needs)
  const [state, setState] = useState(null)
  const [busy, setBusy] = useState(false)
  const [busyLabel, setBusyLabel] = useState('')
  const [error, setError] = useState(null)
  const [monitoring, setMonitoring] = useState(false)
  const stepsRef = useRef(null)
  const esRef = useRef(null)

  useEffect(() => {
    api('/api/health').then(setHealth).catch(() => setHealth(null))
  }, [])

  // Live step trace. The SSE stream is what proves the agent is working rather
  // than a spinner claiming it is.
  useEffect(() => {
    if (!state?.session_id) return
    if (esRef.current) esRef.current.close()
    const es = new EventSource(`/api/stream/${state.session_id}`)
    es.onmessage = (evt) => {
      const msg = JSON.parse(evt.data)
      if (msg.type === 'step') {
        setState((prev) => (prev ? { ...prev, steps: [...prev.steps, msg.step] } : prev))
      } else if (msg.type === 'monitor' || msg.type === 'snapshot') {
        if (msg.state) setState(msg.state)
      }
    }
    es.onerror = () => {}
    esRef.current = es
    return () => es.close()
  }, [state?.session_id])

  useEffect(() => {
    const el = stepsRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [state?.steps?.length])

  const run = useCallback(async () => {
    setBusy(true)
    setBusyLabel('Planning')
    setError(null)
    try {
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
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
      setBusyLabel('')
    }
  }, [query, needs, state?.session_id])

  const act = useCallback(
    async (label, fn) => {
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
    },
    []
  )

  const triggerClosure = () =>
    act('Publishing closure', async () => {
      await api('/api/demo/trigger-closure', { method: 'POST' })
      const r = await api(`/api/session/${state.session_id}/monitor/check`, { method: 'POST' })
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

  const askCamera = () =>
    act('Asking the agent', async () => {
      const r = await api(`/api/session/${state.session_id}/message`, {
        method: 'POST',
        body: JSON.stringify({
          message:
            'Check the fire camera to visually confirm where the fire front is right now.',
        }),
      })
      setState(r)
    })

  const askReturn = () =>
    act('Asking the agent', async () => {
      const r = await api(`/api/session/${state.session_id}/message`, {
        method: 'POST',
        body: JSON.stringify({ message: 'Can I go home yet?' }),
      })
      setState(r)
    })

  const toggleMonitor = () =>
    act(monitoring ? 'Stopping' : 'Starting', async () => {
      const path = `/api/session/${state.session_id}/monitor/${monitoring ? 'stop' : 'start'}`
      await api(path, { method: 'POST' })
      setMonitoring(!monitoring)
    })

  const verdict = state?.verdict
  const steps = state?.steps || []

  const firstSimIndex = useMemo(() => steps.findIndex((s) => s.simulated), [steps])

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          Always-On Wildfire Evacuation Agent
          <span>Spokane County</span>
        </div>

        {health && (
          <>
            <span className={`chip ${health.replay ? 'replay' : 'live'}`}>
              ● {health.replay ? 'REPLAY' : 'LIVE'}
              {health.replay && health.scenario ? ` · ${health.scenario.name}` : ''}
            </span>
            <span className={`chip ${health.policy_enforced ? 'good' : 'bad'}`}>
              <span className={`dot ${health.policy_enforced ? 'on' : 'off'}`} />
              OpenShell {health.policy_enforced ? 'enforcing' : 'NOT enforcing'}
            </span>
            <span className={`chip ${health.inference.ok ? 'good' : 'bad'}`}>
              <span className={`dot ${health.inference.ok ? 'on' : 'off'}`} />
              {health.inference.model.split('/').pop()}
            </span>
          </>
        )}

        <div className="spacer" />

        {state && (
          <>
            <button className="btn sm" onClick={toggleMonitor} disabled={busy}>
              {monitoring ? '■ Stop monitor' : '▶ Start monitor'}
            </button>
            <button className="btn sm danger" onClick={triggerClosure} disabled={busy}>
              ⚡ Simulate road closure
            </button>
          </>
        )}
      </header>

      <div className="main">
        <div className="left">
          {/* ---------- input ---------- */}
          <section className="panel">
            <h3>Resident</h3>
            <div className="body">
              <label className="field" htmlFor="loc">Location — landmark or address</label>
              <input
                id="loc"
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && !busy && run()}
                placeholder="e.g. Rifle Club Road, Spokane County"
              />

              <div className="needs">
                {Object.entries(NEED_LABELS).map(([key, label]) => (
                  <label key={key} className={`need ${needs[key] ? 'on' : ''}`}>
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
                  {busy ? <><span className="spin" /> {busyLabel}…</> : 'Plan my evacuation'}
                </button>
                {state && (
                  <button className="btn ghost" onClick={askReturn} disabled={busy}>
                    Can I go home?
                  </button>
                )}
              </div>

              {error && <div className="warn" style={{ marginTop: 10 }}>{error}</div>}
            </div>
          </section>

          {/* ---------- verdict ---------- */}
          {verdict && (
            <section className={`panel verdict ${levelClass(verdict.level)}`}>
              <h3>
                Recommendation
                <span className="count">— decided by the safety guard, not the model</span>
              </h3>
              <div className="body">
                <p className="headline">{verdict.headline}</p>
                <p className="action">{verdict.recommended_action}</p>

                <dl className="kv">
                  {verdict.destination && (<><dt>Go to</dt><dd>{verdict.destination}</dd></>)}
                  {verdict.route_summary && (<><dt>Route</dt><dd>{verdict.route_summary}</dd></>)}
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
                  <dd style={{ color: 'var(--muted)', fontSize: 11.5 }}>{verdict.freshness_summary}</dd>
                </dl>

                {verdict.critical_warnings?.length > 0 && (
                  <div className="warn">
                    <ul>{verdict.critical_warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
                  </div>
                )}
                {verdict.unverified?.length > 0 && (
                  <div className="unver">
                    <ul>{verdict.unverified.map((u, i) => <li key={i}>{u}</li>)}</ul>
                  </div>
                )}

                {verdict.narrative && (
                  <div className="narrative">
                    <span className="tag">MODEL-WRITTEN SUMMARY — RENDERED AROUND THE VERDICT ABOVE</span>
                    {verdict.narrative}
                  </div>
                )}
              </div>
            </section>
          )}

          {/* ---------- monitor events ---------- */}
          {state?.monitor_events?.length > 0 && (
            <section className="panel">
              <h3>Autonomous actions <span className="count">no user message involved</span></h3>
              <div className="body" style={{ display: 'grid', gap: 8 }}>
                {state.monitor_events.map((e, i) => (
                  <div key={i} className="banner">
                    {e.simulated && <b>SIMULATED EVENT · </b>}
                    {e.changes.join('; ')}
                    {e.replanned && (
                      <>
                        <br />Route <b>{e.previous_route}</b> invalidated → replanned to{' '}
                        <b>{e.new_route || 'no safe route'}</b>
                      </>
                    )}
                    {e.notification && (
                      <><br /><span style={{ color: 'var(--muted)' }}>Notification prepared: {e.notification}</span></>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* ---------- shelters ---------- */}
          {state?.shelters?.length > 0 && (
            <section className="panel">
              <h3>
                Shelters
                <span className="count">
                  — hard constraints applied before distance
                </span>
              </h3>
              <div className="scroll-fade">
                {state.destination && (
                  <div className="item picked">
                    <div className="row">
                      <span className="name">{state.destination.name}</span>
                      <span className="tagline ok">✓ SELECTED</span>
                    </div>
                    <div className="meta">
                      {state.destination.address} · {state.destination.distance_km?.toFixed(1)} km
                    </div>
                    <div className="caps">
                      {(state.destination.capabilities || []).map((c) => (
                        <span className="cap" key={c}>{c}</span>
                      ))}
                    </div>
                  </div>
                )}
                {(state.rejected_shelters || []).map(({ shelter, unmet }) => (
                  <div className="item out" key={shelter.shelter_id}>
                    <div className="row">
                      <span className="name">{shelter.name}</span>
                      <span className="tagline no">✗ REJECTED</span>
                    </div>
                    <div className="meta">
                      {shelter.distance_km?.toFixed(1)} km — closer, but does not meet every requirement
                    </div>
                    <div className="caps">
                      {unmet.map((u) => <span className="cap missing" key={u}>missing {u}</span>)}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* ---------- routes ---------- */}
          {(state?.approved_routes?.length > 0 || state?.rejected_routes?.length > 0) && (
            <section className="panel">
              <h3>Route candidates <span className="count">— validated, not just generated</span></h3>
              <div>
                {(state.approved_routes || []).map((r) => (
                  <div className={`item ${state.current_route?.route_id === r.route_id ? 'picked' : ''}`} key={r.route_id}>
                    <div className="row">
                      <span className="name">{r.route_id}</span>
                      <span className="tagline ok">
                        {state.current_route?.route_id === r.route_id ? '✓ SELECTED' : '✓ approved'}
                      </span>
                    </div>
                    <div className="meta">
                      {r.distance_km} km · {Math.round(r.eta_min)} min
                      {r.hazard_margin_km != null && ` · ${r.hazard_margin_km.toFixed(1)} km hazard margin`}
                    </div>
                  </div>
                ))}
                {(state.rejected_routes || []).map((r) => (
                  <div className="item out" key={r.route_id}>
                    <div className="row">
                      <span className="name">{r.route_id}</span>
                      <span className="tagline no">✗ REJECTED</span>
                    </div>
                    <div className="meta">
                      {r.distance_km} km · {Math.round(r.eta_min)} min — {r.rejection_reason}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* ---------- blocked ---------- */}
          {state?.blocked?.length > 0 && (
            <section className="panel">
              <h3 style={{ color: 'var(--blocked)' }}>Blocked actions <span className="count">— refused outside the agent</span></h3>
              <div>
                {state.blocked.map((b, i) => (
                  <div className="item" key={i}>
                    <div className="row">
                      <span className="name" style={{ color: 'var(--blocked)' }}>{b.tool}</span>
                      <span className="tagline" style={{ color: 'var(--blocked)' }}>⛔ BLOCKED</span>
                    </div>
                    <div className="meta">
                      {b.host}{b.path} · policy <code>{b.policy}</code> · layer <code>{b.layer}</code>
                      <br />{b.detail}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* ---------- sources ---------- */}
          {state?.sources?.length > 0 && (
            <section className="panel">
              <h3>Sources <span className="count">every answer carries its provenance</span></h3>
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
            </section>
          )}

          {/* ---------- containment demos ---------- */}
          {state && (
            <section className="panel">
              <h3>Containment checks</h3>
              <div className="body" style={{ display: 'grid', gap: 7 }}>
                <button className="btn sm" onClick={askCamera} disabled={busy}>
                  Ask the agent to use the fire camera
                </button>
                <button className="btn sm" onClick={askUnapproved} disabled={busy}>
                  Send the plan to an unapproved recipient
                </button>
                <div style={{ fontSize: 11.5, color: 'var(--dim)', lineHeight: 1.5 }}>
                  Both are real capabilities. The first is refused by the OpenShell L7
                  proxy because its host is absent from{' '}
                  <code>policies/spokane-evac.yaml</code>; the second by the session's
                  action scope. Neither refusal is simulated by the agent.
                </div>
              </div>
            </section>
          )}
        </div>

        <div className="right">
          <MapPanel state={state} />
          {!state && (
            <div className="mapnote">
              <b>Ready.</b> Enter a location and household needs, then plan an
              evacuation. The map will show evacuation zones, fire perimeters,
              candidate routes and which of them were rejected.
              {health?.replay && health.scenario && (
                <>
                  <br /><br />
                  <b>{health.scenario.label}: {health.scenario.name}.</b>{' '}
                  {health.scenario.summary}
                </>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ---------- activity ---------- */}
      <div className="activity">
        <h3>
          Agent activity
          <span style={{ color: 'var(--dim)', fontWeight: 400, letterSpacing: 0, textTransform: 'none' }}>
            observable execution only — tool calls, outcomes, timings, decisions
          </span>
          {monitoring && (
            <span className="chip good" style={{ marginLeft: 'auto' }}>
              <span className="dot on" /> monitoring
            </span>
          )}
        </h3>
        <div className="steps" ref={stepsRef}>
          {steps.length === 0 && (
            <div className="empty">
              No activity yet. Every tool call the agent makes will appear here with
              its arguments, its outcome and how long it took.
            </div>
          )}
          {steps.map((s, i) => (
            <div key={`${s.seq}-${i}`}>
              {i === firstSimIndex && firstSimIndex >= 0 && (
                <div className="sep">--- simulated event ---</div>
              )}
              <div className={`step ${KIND_CLASS[s.kind] || ''} ${s.simulated ? 'sim' : ''}`}>
                <span className="ts">[{timeOf(s.at)}]</span>
                <span className="kind">{KIND_TAG[s.kind] || s.kind}</span>
                <span className="lbl">{s.label}</span>
                <span className="det">{s.detail || ''}</span>
                {s.latency_ms != null && <span className="ms">{s.latency_ms}ms</span>}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
