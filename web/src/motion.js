/**
 * Motion system.
 *
 * Apple's fluid-interface model, translated to Motion's spring API. Two things
 * govern every choice in this file:
 *
 *   1. A spring has no duration. Motion's `bounce` + `duration` map onto
 *      Apple's damping ratio + response, so those are the only two knobs used.
 *   2. This is an emergency interface. Overshoot is reserved for motion the
 *      user's own gesture put energy into — a flick, a drag release. Nothing
 *      that merely appears is allowed to bounce, because a wildfire evacuation
 *      screen should read as calm and certain, not playful.
 */

/** Critically damped. The house default: graceful, never distracting. */
export const SPRING = { type: 'spring', bounce: 0, duration: 0.4 }

/** Snappier critical damping, for small elements and press feedback. */
export const SPRING_QUICK = { type: 'spring', bounce: 0, duration: 0.28 }

/**
 * Under-damped. Only legal when a gesture carried momentum into the motion.
 * Apple ships damping ~0.8 / response 0.3 for drawers; bounce 0.2 is the
 * closest Motion equivalent.
 */
export const SPRING_MOMENTUM = { type: 'spring', bounce: 0.2, duration: 0.35 }

/**
 * Project where a flick would come to rest, so we snap to the target the
 * gesture was aimed at rather than the one it happened to be nearest on
 * release. This is the exponential-decay form from Apple's sample code, not
 * the textbook v^2/(2a) — they behave noticeably differently.
 *
 * @param velocity px/s at release
 * @param decelerationRate 0.998 for normal scroll feel, 0.99 for snappier
 */
export function project(velocity, decelerationRate = 0.998) {
  return ((velocity / 1000) * decelerationRate) / (1 - decelerationRate)
}

/**
 * Progressive resistance past a boundary. A hard stop reads as frozen; falling
 * behind the finger reads as "responsive, but there is nothing more here".
 */
export function rubberband(overshoot, dimension, constant = 0.55) {
  return (overshoot * dimension * constant) / (dimension + constant * Math.abs(overshoot))
}

/** Clamp with rubber-banding outside [min, max] rather than a hard cut. */
export function clampRubber(value, min, max, dimension) {
  if (value < min) return min - rubberband(min - value, dimension)
  if (value > max) return max + rubberband(value - max, dimension)
  return value
}

/**
 * Rolling pointer history, so release velocity comes from the last few frames
 * rather than the final two events. A single-sample velocity is noisy enough
 * to send a flick to the wrong snap point.
 */
export class VelocityTracker {
  constructor(windowMs = 100) {
    this.windowMs = windowMs
    this.samples = []
  }

  add(value) {
    const now = performance.now()
    this.samples.push({ value, t: now })
    while (this.samples.length > 2 && now - this.samples[0].t > this.windowMs) {
      this.samples.shift()
    }
  }

  /** px/s over the retained window. */
  velocity() {
    if (this.samples.length < 2) return 0
    const first = this.samples[0]
    const last = this.samples[this.samples.length - 1]
    const dt = (last.t - first.t) / 1000
    if (dt <= 0) return 0
    return (last.value - first.value) / dt
  }

  reset() {
    this.samples = []
  }
}

/** The nearest value in `points` to `target`. */
export function nearestSnap(target, points) {
  return points.reduce((best, p) =>
    Math.abs(p - target) < Math.abs(best - target) ? p : best
  )
}
