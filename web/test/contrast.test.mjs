/**
 * Contrast tests for the palette.
 *
 * Colour is load-bearing here: the hazard scale is how someone reads their
 * evacuation level, and the feedback tiers are how they tell a warning from a
 * gap in coverage. "Looks fine on my monitor" is not a check, and the values
 * that fail are rarely the ones you would guess — Apple's own #007aff carries
 * white text at 4.02:1, under AA for a 13px label.
 *
 * Parsed straight out of styles.css so the tests fail when the palette drifts,
 * not when someone remembers to update a duplicated copy here.
 */

import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const css = readFileSync(
  fileURLToPath(new URL('../src/styles.css', import.meta.url)),
  'utf8'
)

/**
 * Split the two arguments of a `light-dark(...)` at the top level.
 *
 * A naive `([^,]+),(...)` split breaks on `rgba(60, 60, 67, 0.72)`, which is
 * most of the palette — the commas inside the nested function come first.
 */
function splitArgs(body) {
  const args = []
  let depth = 0
  let current = ''
  for (const ch of body) {
    if (ch === '(') depth++
    if (ch === ')') depth--
    if (ch === ',' && depth === 0) {
      args.push(current.trim())
      current = ''
    } else {
      current += ch
    }
  }
  args.push(current.trim())
  return args
}

/** Pull `--token: light-dark(a, b)` out of the stylesheet. */
function pair(name) {
  const at = css.indexOf(`${name}: light-dark(`)
  assert.ok(at !== -1, `token ${name} not found as a light-dark() pair`)

  const open = css.indexOf('(', at + name.length)
  let depth = 0
  let close = open
  for (; close < css.length; close++) {
    if (css[close] === '(') depth++
    else if (css[close] === ')' && --depth === 0) break
  }

  const [light, dark] = splitArgs(css.slice(open + 1, close))
  return { light, dark }
}

function parse(color) {
  const c = color.trim()
  if (c.startsWith('#')) {
    const h = c.slice(1)
    const full = h.length === 3 ? [...h].map((x) => x + x).join('') : h
    return [
      parseInt(full.slice(0, 2), 16),
      parseInt(full.slice(2, 4), 16),
      parseInt(full.slice(4, 6), 16),
      1,
    ]
  }
  const m = c.match(/rgba?\(([^)]+)\)/)
  assert.ok(m, `cannot parse colour: ${color}`)
  const parts = m[1].split(',').map((x) => parseFloat(x.trim()))
  return [parts[0], parts[1], parts[2], parts[3] ?? 1]
}

/** Flatten a translucent colour onto an opaque background. */
function over(fg, bg) {
  const [r, g, b, a] = parse(fg)
  const [br, bgc, bb] = parse(bg)
  return [r * a + br * (1 - a), g * a + bgc * (1 - a), b * a + bb * (1 - a), 1]
}

function luminance(rgb) {
  const [r, g, b] = rgb.map((v) => {
    const s = v / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(fg, bg) {
  const a = luminance(over(fg, bg))
  const b = luminance(parse(bg))
  const [hi, lo] = a > b ? [a, b] : [b, a]
  return (hi + 0.05) / (lo + 0.05)
}

// Card surfaces the text actually sits on, per theme.
const CARD = { light: '#ffffff', dark: '#161b26' }

const AA_TEXT = 4.5 // body copy
const AA_LARGE = 3.0 // secondary/tertiary labels and non-text indicators

function bothThemes(token, minimum, label = token) {
  const p = pair(token)
  for (const theme of ['light', 'dark']) {
    const r = contrast(p[theme], CARD[theme])
    assert.ok(
      r >= minimum,
      `${label} in ${theme}: ${r.toFixed(2)}:1, needs ${minimum}:1 (${p[theme]})`
    )
  }
}

test('primary label clears AA for body copy in both themes', () => {
  bothThemes('--text', AA_TEXT)
})

test('secondary label clears AA for body copy in both themes', () => {
  bothThemes('--text-secondary', AA_TEXT)
})

test('tertiary and quaternary labels clear the large-text threshold', () => {
  bothThemes('--text-tertiary', AA_LARGE)
  bothThemes('--text-quaternary', AA_LARGE)
})

test('every feedback colour is readable as text on a card', () => {
  for (const token of ['--error', '--warning', '--completion', '--status']) {
    bothThemes(token, AA_TEXT)
  }
})

test('blocked-action purple is readable, since it names a refused capability', () => {
  bothThemes('--blocked', AA_TEXT)
})

test('white on the primary button fill clears AA', () => {
  // The fill is the darker stop of the gradient — the worst case for the label
  // is the lighter stop, so check that one.
  const m = css.match(/--btn-primary-bg: linear-gradient\(\s*180deg,\s*light-dark\(([^,]+),\s*([^)]+)\)/)
  assert.ok(m, 'primary button gradient not found')
  for (const [theme, stop] of [['light', m[1].trim()], ['dark', m[2].trim()]]) {
    const r = contrast('#ffffff', stop)
    assert.ok(r >= AA_TEXT, `white on primary fill (${theme}, ${stop}): ${r.toFixed(2)}:1`)
  }
})

test('accent-strong is readable as text, unlike the raw system blue', () => {
  bothThemes('--accent-strong', AA_TEXT)

  // Guard the reason this token exists: if someone "simplifies" it back to
  // --accent, this records why that regresses.
  const raw = pair('--accent')
  assert.ok(
    contrast(raw.light, CARD.light) < AA_TEXT,
    'if #007aff now passes, the accent split can be revisited'
  )
})

test('hazard colours are distinguishable from each other on the map', () => {
  // Level 2 and Level 3 sitting adjacent must not read as the same colour to
  // someone glancing at a map under stress.
  for (const theme of ['light', 'dark']) {
    const l2 = luminance(parse(pair('--map-l2')[theme]))
    const l3 = luminance(parse(pair('--map-l3')[theme]))
    assert.notEqual(
      l2.toFixed(2),
      l3.toFixed(2),
      `map Level 2 and Level 3 have the same luminance in ${theme}`
    )
  }
})

test('map hazard colours are visible against their basemap', () => {
  // Light basemap is near-white; dark basemap is near-black.
  const BASEMAP = { light: '#f2f2f2', dark: '#1a1a1a' }
  for (const token of ['--map-l3', '--map-l2', '--map-l1', '--map-fire', '--map-route']) {
    const p = pair(token)
    for (const theme of ['light', 'dark']) {
      const r = contrast(p[theme], BASEMAP[theme])
      assert.ok(
        r >= AA_LARGE,
        `${token} on the ${theme} basemap: ${r.toFixed(2)}:1, needs ${AA_LARGE}:1`
      )
    }
  }
})
