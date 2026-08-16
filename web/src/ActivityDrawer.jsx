import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { useDrawer } from './useDrawer.js'
import { SPRING_QUICK } from './motion.js'

const KIND_TAG = {
  model: 'MODEL',
  skill: 'LEARN',
  tool: 'TOOL',
  'safety guard': 'GUARD',
  monitor: 'MONITOR',
  blocked: 'BLOCKED',
}

const KIND_CLASS = {
  model: 'k-model',
  skill: 'k-skill',
  tool: 'k-tool',
  'safety guard': 'k-guard',
  monitor: 'k-monitor',
  blocked: 'k-blocked',
}

// Peek shows the handle and title only; the middle stop is the working height;
// the top stop is for when the trace itself is the subject of conversation.
// The top stop is viewport-relative, so it is recomputed on resize rather than
// frozen at whatever the window happened to be when the module loaded.
function heightsFor(viewportHeight) {
  return [44, 232, Math.max(300, Math.round(viewportHeight * 0.62))]
}

function timeOf(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString([], { hour12: false })
}

export default function ActivityDrawer({ steps, monitoring, firstSimIndex }) {
  const reduceMotion = useReducedMotion()

  const [viewportHeight, setViewportHeight] = useState(() =>
    typeof window === 'undefined' ? 800 : window.innerHeight
  )

  useEffect(() => {
    const onResize = () => setViewportHeight(window.innerHeight)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const heights = useMemo(() => heightsFor(viewportHeight), [viewportHeight])

  const { height, snapIndex, dragging, snapTo, handleProps } = useDrawer({
    heights,
    initial: 1,
  })

  const scroller = useRef(null)
  const wasAtBottom = useRef(true)
  const [scrolled, setScrolled] = useState(false)

  // Only auto-scroll if the reader was already at the bottom. Yanking the view
  // away from someone who scrolled up to read an earlier step is the kind of
  // detail that reads as carelessness.
  useLayoutEffect(() => {
    const el = scroller.current
    if (!el || !wasAtBottom.current) return
    el.scrollTo({
      top: el.scrollHeight,
      behavior: reduceMotion ? 'auto' : 'smooth',
    })
  }, [steps.length, reduceMotion])

  useEffect(() => {
    const el = scroller.current
    if (!el) return
    const onScroll = () => {
      wasAtBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24
      setScrolled(el.scrollTop > 2)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  return (
    <motion.aside
      className={`drawer ${dragging ? 'dragging' : ''}`}
      style={{ height }}
      aria-label="Agent activity"
    >
      <div className="drawer-handle" {...handleProps}>
        <span className="grip" aria-hidden="true" />
        <span className="t-label">Agent activity</span>
        <span className="note t-caption" style={{ color: 'var(--text-quaternary)' }}>
          observable execution only — tool calls, outcomes, timings, decisions
        </span>
        <span style={{ flex: 1 }} />
        {monitoring && (
          <span className="chip good">
            <span className="dot pulse" />
            monitoring
          </span>
        )}
        <button
          className="btn sm ghost"
          onClick={() => snapTo(snapIndex >= heights.length - 1 ? 0 : snapIndex + 1)}
          // The handle owns the drag; this button owns the click. Without this
          // the pointerdown would start a drag and the click would never fire.
          onPointerDown={(e) => e.stopPropagation()}
          aria-label={snapIndex >= heights.length - 1 ? 'Collapse panel' : 'Expand panel'}
        >
          {snapIndex >= heights.length - 1 ? '▾' : '▴'}
        </button>
      </div>

      <div className={`steps ${scrolled ? 'scrolled' : ''}`} ref={scroller}>
        {steps.length === 0 && (
          <p className="empty t-callout">
            No activity yet. Every tool call the agent makes appears here with its
            arguments, its outcome and how long it took — never its reasoning.
          </p>
        )}

        {steps.map((s, i) => (
          <div key={`${s.seq}-${i}`}>
            {i === firstSimIndex && firstSimIndex >= 0 && (
              <div className="sep">--- simulated event ---</div>
            )}
            <motion.div
              initial={reduceMotion ? false : { opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              transition={SPRING_QUICK}
              className={`step ${KIND_CLASS[s.kind] || ''} ${s.simulated ? 'sim' : ''}`}
            >
              <span className="ts">[{timeOf(s.at)}]</span>
              <span className="kind">{KIND_TAG[s.kind] || s.kind}</span>
              <span className="lbl">{s.label}</span>
              <span className="det">{s.detail || ''}</span>
              {s.latency_ms != null && <span className="ms">{s.latency_ms}ms</span>}
            </motion.div>
          </div>
        ))}
      </div>
    </motion.aside>
  )
}
