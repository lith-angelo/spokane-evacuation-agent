import { useCallback, useEffect, useState } from 'react'

/**
 * Theme control.
 *
 * Three states, not two: `system` follows the OS and is the default, because
 * the right answer for most people is the one they already chose once. An
 * explicit `light` or `dark` overrides it and persists.
 *
 * `color-scheme` on the root element is the only thing this sets, because the
 * stylesheet's palette is built from `light-dark()` and resolves against it.
 * That also gets form controls, scrollbars and the browser's own chrome to
 * match for free, instead of leaving them stubbornly light.
 *
 * `resolved` is still computed, because the map needs to know which basemap
 * tiles to load and CSS cannot tell it.
 */

const KEY = 'evac.theme'
const MODES = ['system', 'light', 'dark']

function systemTheme() {
  if (typeof window === 'undefined') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function stored() {
  if (typeof localStorage === 'undefined') return 'system'
  const v = localStorage.getItem(KEY)
  return MODES.includes(v) ? v : 'system'
}

export function useTheme() {
  const [mode, setMode] = useState(stored)
  const [resolved, setResolved] = useState(() =>
    stored() === 'system' ? systemTheme() : stored()
  )

  // Re-resolve whenever the choice changes or, while on `system`, whenever the
  // OS flips underneath us.
  useEffect(() => {
    const apply = () => setResolved(mode === 'system' ? systemTheme() : mode)
    apply()

    if (mode !== 'system') return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    mq.addEventListener('change', apply)
    return () => mq.removeEventListener('change', apply)
  }, [mode])

  useEffect(() => {
    // `light dark` lets the browser follow the OS itself; a single keyword pins
    // it. Either way `light-dark()` in the stylesheet resolves against this.
    document.documentElement.style.colorScheme = mode === 'system' ? 'light dark' : mode
  }, [mode, resolved])

  const cycle = useCallback(() => {
    setMode((current) => {
      const next = MODES[(MODES.indexOf(current) + 1) % MODES.length]
      try {
        localStorage.setItem(KEY, next)
      } catch {
        // Private browsing. The choice still applies for this session.
      }
      return next
    })
  }, [])

  return { mode, resolved, cycle }
}
