/**
 * A drag-to-resize drawer for the agent activity trace.
 *
 * This is the one surface in the app that earns direct manipulation. During a
 * demo you want the trace small while the map is the subject and large while
 * the trace is the subject, and a drag is the fastest way to say which. A
 * decorative gesture on a safety screen would be indefensible; this one does
 * real work.
 *
 * Implements, in order: 1:1 tracking from the grab offset, a movement
 * threshold before committing, rubber-banding at both ends, momentum
 * projection to pick the snap point, velocity handoff into the spring, and
 * interruption — grabbing a drawer that is still settling picks it up from
 * wherever it currently is, at whatever speed it currently has.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { animate, useMotionValue, useReducedMotion } from 'motion/react'
import {
  SPRING,
  SPRING_MOMENTUM,
  VelocityTracker,
  clampRubber,
  nearestSnap,
  project,
} from './motion.js'

// Below this, a pointer-down is a click on the header, not a drag.
const DRAG_THRESHOLD_PX = 6

// A flick faster than this commits to the next snap point in its direction
// regardless of distance travelled. Velocity sign is a better read of intent
// than final position — a fast short flick means "go", a slow long drag that
// stops means "put it back".
const FLICK_VELOCITY = 420

export function useDrawer({ heights, initial = 1 }) {
  const reduceMotion = useReducedMotion()

  // `height` is the drawer's visible height in px, and it really is `height`
  // that animates — not a transform.
  //
  // The usual advice is to translate a max-height sheet instead, because
  // transforms stay on the compositor. That works for a sheet that slides off
  // the bottom of the screen, but this drawer must keep its handle and its
  // newest trace lines visible at every size: translating down would push the
  // live end of the log out of view, which is the one part anyone is reading.
  // One element with a shallow subtree relaying at 60fps is a cost worth paying
  // to keep the content correct. Everything else in the app animates transform
  // and opacity only.
  const maxHeight = heights[heights.length - 1]
  const height = useMotionValue(heights[initial])
  const [snapIndex, setSnapIndex] = useState(initial)
  const [dragging, setDragging] = useState(false)

  const drag = useRef(null)
  const tracker = useMemo(() => new VelocityTracker(), [])
  const animation = useRef(null)

  const snapTo = useCallback(
    (index, velocity = 0) => {
      const clamped = Math.max(0, Math.min(heights.length - 1, index))
      setSnapIndex(clamped)

      animation.current?.stop()

      if (reduceMotion) {
        // Reduced motion still needs the state change to be visible; it just
        // must not travel. Set it directly rather than sliding.
        height.set(heights[clamped])
        return
      }

      // Bounce only because a gesture put energy in. A programmatic snap (a
      // keyboard press, a click on the handle) gets the calm spring.
      const spring = Math.abs(velocity) > 40 ? SPRING_MOMENTUM : SPRING
      animation.current = animate(height, heights[clamped], {
        ...spring,
        velocity,
      })
    },
    [height, heights, reduceMotion]
  )

  const onPointerDown = useCallback(
    (event) => {
      if (event.button !== undefined && event.button !== 0) return

      // Interruption: read the live on-screen value and start from there. Using
      // the logical target instead would make a grabbed, still-moving drawer
      // jump before it followed the finger.
      const current = height.get()
      animation.current?.stop()

      event.currentTarget.setPointerCapture?.(event.pointerId)
      tracker.reset()
      tracker.add(current)

      drag.current = {
        startY: event.clientY,
        startHeight: current,
        committed: false,
      }
    },
    [height, tracker]
  )

  const onPointerMove = useCallback(
    (event) => {
      const state = drag.current
      if (!state) return

      // Dragging up grows the drawer, so height moves opposite to clientY.
      const delta = state.startY - event.clientY

      if (!state.committed) {
        if (Math.abs(delta) < DRAG_THRESHOLD_PX) return
        state.committed = true
        setDragging(true)
      }

      const next = clampRubber(
        state.startHeight + delta,
        heights[0],
        maxHeight,
        maxHeight
      )

      // 1:1 with the pointer for the whole gesture, not just at the end.
      height.set(next)
      tracker.add(next)
    },
    [height, heights, maxHeight, tracker]
  )

  const endDrag = useCallback(
    (event) => {
      const state = drag.current
      drag.current = null
      if (!state) return

      event?.currentTarget?.releasePointerCapture?.(event.pointerId)

      if (!state.committed) {
        setDragging(false)
        return
      }

      setDragging(false)

      const velocity = tracker.velocity()
      const current = height.get()

      let targetIndex
      if (Math.abs(velocity) > FLICK_VELOCITY) {
        // Committed flick: move one stop in the direction of travel.
        const from = nearestSnap(state.startHeight, heights)
        const fromIndex = heights.indexOf(from)
        targetIndex = fromIndex + (velocity > 0 ? 1 : -1)
      } else {
        // Otherwise land where the momentum was actually headed.
        const projected = current + project(velocity)
        targetIndex = heights.indexOf(nearestSnap(projected, heights))
      }

      snapTo(targetIndex, velocity)
    },
    [height, heights, snapTo, tracker]
  )

  // Keyboard parity: a drag-only affordance is not an affordance for everyone.
  const onKeyDown = useCallback(
    (event) => {
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        snapTo(snapIndex + 1)
      } else if (event.key === 'ArrowDown') {
        event.preventDefault()
        snapTo(snapIndex - 1)
      } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        snapTo(snapIndex >= heights.length - 1 ? 0 : snapIndex + 1)
      }
    },
    [heights.length, snapIndex, snapTo]
  )

  useEffect(() => () => animation.current?.stop(), [])

  return {
    height,
    maxHeight,
    snapIndex,
    dragging,
    snapTo,
    handleProps: {
      onPointerDown,
      onPointerMove,
      onPointerUp: endDrag,
      onPointerCancel: endDrag,
      onKeyDown,
      role: 'separator',
      tabIndex: 0,
      'aria-orientation': 'horizontal',
      'aria-label': 'Resize the agent activity panel',
      'aria-valuenow': snapIndex,
      'aria-valuemin': 0,
      'aria-valuemax': heights.length - 1,
    },
  }
}
