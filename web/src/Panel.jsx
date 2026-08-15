import { motion } from 'motion/react'
import { SPRING } from './motion.js'

/**
 * A titled card in the left rail.
 *
 * Panels appear as results arrive, so they enter along the axis the rail
 * grows — downward — and never from a random direction. Enter and exit share
 * the same path, so a panel that slides down to appear slides up to leave.
 *
 * The entrance is critically damped. Nothing here was thrown by the user, so
 * nothing here is allowed to overshoot.
 */
export default function Panel({ title, note, children, tone, className = '' }) {
  return (
    <motion.section
      initial={{ opacity: 0, y: -8, scale: 0.99 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -8, scale: 0.99 }}
      transition={SPRING}
      className={`panel ${className}`}
    >
      {title && (
        <header>
          <span className="t-label" style={tone ? { color: tone } : undefined}>
            {title}
          </span>
          {note && <span className="note">{note}</span>}
        </header>
      )}
      {children}
    </motion.section>
  )
}
