/**
 * Tests for the motion math.
 *
 * These are the functions whose behaviour is easy to get subtly wrong and
 * impossible to eyeball: an easing that looks fine at demo speed can still be
 * using the wrong projection model. Run with `npm test`.
 */

import assert from 'node:assert/strict'
import test from 'node:test'
import {
  clampRubber,
  nearestSnap,
  project,
  rubberband,
  VelocityTracker,
} from '../src/motion.js'

test('project: exponential decay, not the textbook quadratic', () => {
  // v^2/(2a) would make doubling the velocity quadruple the throw. Apple's
  // form is linear in velocity, and the difference is very visible in the hand.
  assert.ok(Math.abs(project(2000) / project(1000) - 2) < 1e-9)
})

test('project: signed, symmetric, and zero at rest', () => {
  assert.equal(project(0), 0)
  assert.ok(project(1000) > 0)
  assert.ok(project(-1000) < 0)
  assert.ok(Math.abs(project(1000) + project(-1000)) < 1e-9)
})

test('project: a lower deceleration rate throws a shorter distance', () => {
  assert.ok(project(1000, 0.99) < project(1000, 0.998))
})

test('rubberband: always falls behind the finger', () => {
  for (const overshoot of [1, 10, 100, 500, 2000]) {
    assert.ok(rubberband(overshoot, 500) < overshoot)
  }
})

test('rubberband: resistance increases with distance past the bound', () => {
  const ratios = [10, 50, 100, 400].map((o) => rubberband(o, 500) / o)
  for (let i = 1; i < ratios.length; i++) {
    assert.ok(ratios[i] < ratios[i - 1], 'each step should follow proportionally less')
  }
})

test('rubberband: bounded, so a hard drag never runs away', () => {
  assert.ok(rubberband(100000, 500) < 500)
})

test('clampRubber: passes through inside the range, resists outside it', () => {
  assert.equal(clampRubber(150, 100, 300, 300), 150)

  const below = clampRubber(50, 100, 300, 300)
  assert.ok(below > 50 && below < 100, 'resists toward the minimum without snapping to it')

  const above = clampRubber(350, 100, 300, 300)
  assert.ok(above > 300 && above < 350, 'resists past the maximum without hard-stopping')
})

test('nearestSnap: picks the closest stop', () => {
  const heights = [44, 232, 500]
  assert.equal(nearestSnap(40, heights), 44)
  assert.equal(nearestSnap(230, heights), 232)
  assert.equal(nearestSnap(499, heights), 500)
})

test('VelocityTracker: degenerate inputs report no velocity', () => {
  const t = new VelocityTracker()
  assert.equal(t.velocity(), 0)
  t.add(100)
  assert.equal(t.velocity(), 0, 'a single sample cannot imply motion')
})

test('VelocityTracker: sign follows the direction of travel', async () => {
  const up = new VelocityTracker()
  up.add(0)
  await new Promise((r) => setTimeout(r, 20))
  up.add(100)
  assert.ok(up.velocity() > 0)

  const down = new VelocityTracker()
  down.add(100)
  await new Promise((r) => setTimeout(r, 20))
  down.add(0)
  assert.ok(down.velocity() < 0)
})

test('VelocityTracker: reset clears history', () => {
  const t = new VelocityTracker()
  t.add(0)
  t.add(50)
  t.reset()
  assert.equal(t.velocity(), 0)
})
