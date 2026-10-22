import { expect, test } from 'claude-code/testing'

import { statusText } from '../hooks/ui'
import { DIR, START, attach, setup } from './harness'

const now = 10_000_000
const base = { name: 'demo', done: 2, total: 5, savedAt: null, task: '', now }

test('status text formats counts, age and task', () => {
  expect(statusText({ ...base, savedAt: now - 4 * 60_000 })).toBe('⌂ demo · 2/5 · saved 4m')
  expect(statusText({ ...base, lastActive: undefined })).toBe('⌂ demo · 2/5')
  expect(statusText({ ...base, savedAt: now - 30_000 })).toBe('⌂ demo · 2/5 · saved now')
  expect(statusText({ ...base, savedAt: now - 3 * 3_600_000 })).toBe('⌂ demo · 2/5 · saved 3h')
  expect(statusText({ ...base, savedAt: now - 2 * 86_400_000 })).toBe('⌂ demo · 2/5 · saved 2d')
  const long = statusText({ ...base, task: 'x'.repeat(60) })
  expect(long.endsWith('…')).toBe(true)
  expect(long).toBe(`⌂ demo · 2/5 · ${'x'.repeat(39)}…`)
})

test('status text falls back to last-active in local time', () => {
  const at = new Date(2026, 9, 22, 8, 0).getTime()
  const t = statusText({ ...base, lastActive: '2026-10-22T08:00', now: at + 2 * 3_600_000 })
  expect(t).toBe('⌂ demo · 2/5 · saved 2h')
})

test('status appears on attach and clears on detach', async ($, on) => {
  const { seen } = setup(on)
  await $.session.start(START)
  await attach($, DIR)
  expect(seen.status.at(-1)?.startsWith('⌂ demo · 2/3')).toBe(true)
  await $.tool.call({ tool: 'mcp__workspace__detach', tool_use_id: 'u2' })
  expect(seen.status.at(-1)).toBeUndefined()
})

test('status refreshes every minute', async ($, on) => {
  const { seen, clock } = setup(on, {}, {})
  await $.session.start(START)
  await attach($, DIR)
  const n = seen.status.length
  await clock.advance(60_000)
  await clock.settle()
  expect(seen.status.length).toBeGreaterThan(n)
  await $.tool.call({ tool: 'mcp__workspace__detach', tool_use_id: 'u2' })
  const m = seen.status.length
  await clock.advance(120_000)
  await clock.settle()
  expect(seen.status.length).toBe(m)
})

test('set_task updates state and frontmatter', async ($, on) => {
  const { seen, m } = setup(on)
  await $.session.start(START)
  await attach($, DIR)
  const r = await $.tool.call({ tool: 'mcp__workspace__set_task', tool_use_id: 'u3', task: '  Review proposal  ' })
  expect(r.deny).toBeUndefined()
  expect(m.state.task).toBe('Review proposal')
  expect(seen.writes[0].text).toContain('current-task: Review proposal\n')
  expect(seen.status.at(-1)?.endsWith('· Review proposal')).toBe(true)
})

test('set_task collapses newlines and caps length', async ($, on) => {
  const { seen, m } = setup(on)
  await $.session.start(START)
  await attach($, DIR)
  await $.tool.call({ tool: 'mcp__workspace__set_task', tool_use_id: 'u3', task: 'a\n\nb\r\n  c\td' })
  expect(m.state.task).toBe('a b c d')
  expect(seen.writes[0].text).toContain('current-task: a b c d\n')
  await $.tool.call({ tool: 'mcp__workspace__set_task', tool_use_id: 'u4', task: 'y'.repeat(200) })
  expect(String(m.state.task).length).toBe(120)
})

test('set_task without a project is denied', async ($, on) => {
  const { seen } = setup(on)
  await $.session.start(START)
  const r = await $.tool.call({ tool: 'mcp__workspace__set_task', tool_use_id: 'u3', task: 'x' })
  expect(r.deny).toBeDefined()
  expect(seen.writes).toEqual([])
})
