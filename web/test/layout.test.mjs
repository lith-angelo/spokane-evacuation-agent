import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const css = readFileSync(
  fileURLToPath(new URL('../src/styles.css', import.meta.url)),
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
