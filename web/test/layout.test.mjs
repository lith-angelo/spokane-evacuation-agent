import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const css = readFileSync(
  fileURLToPath(new URL('../src/styles.css', import.meta.url)),
  'utf8'
)
const app = readFileSync(
  fileURLToPath(new URL('../src/App.jsx', import.meta.url)),
  'utf8'
)
const map = readFileSync(
  fileURLToPath(new URL('../src/MapPanel.jsx', import.meta.url)),
  'utf8'
)

test('the left rail owns vertical scrolling', () => {
  const rail = css.match(/\.rail\s*\{([^}]*)\}/)?.[1] || ''

  assert.match(rail, /height:\s*100%/)
  assert.match(rail, /min-height:\s*0/)
  assert.match(rail, /overflow-y:\s*auto/)
  assert.match(rail, /scrollbar-gutter:\s*stable/)
})

test('left-rail cards keep their natural height instead of shrinking', () => {
  const cards = css.match(/\.rail\s*>\s*\.panel\s*\{([^}]*)\}/)?.[1] || ''

  assert.match(cards, /flex:\s*0\s+0\s+auto/)
})

test('boundary copy distinguishes OpenShell policy from application authorization', () => {
  assert.match(app, /Boundary demonstrations/)
  assert.match(app, /OpenShell's L7 proxy/)
  assert.match(app, /Application code running/)
  assert.match(app, /successful message\s+delivery is simulated/)
  assert.doesNotMatch(app, /Both are real capabilities/)
})

test('environmental evidence is labeled without overstating authority', () => {
  assert.match(app, /PM2\.5/)
  assert.match(app, /FIRMS points — not incidents or perimeters/)
  assert.match(map, /FIRMS point evidence, not a perimeter/)
  assert.match(map, /FIRMS thermal detection/)
})

test('source evidence leads with plain-language meaning', () => {
  assert.match(app, /Live evidence/)
  assert.match(app, /Official fire incidents/)
  assert.match(app, /No recent satellite heat detections were found nearby/)
  assert.match(app, /secondary emergency-traffic check failed/)
  assert.match(app, /DEMO DATA/)
})
