import { expect, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

import { stubEngine } from './engine'

const DIR = '/ws/projects/demo'
const DOC = `---
project: demo
type: feature
status: active
---
# Demo
## Reference Files
| File | Content |
|------|---------|
| [design.md](design.md) | Design |
## Progress
- [ ] one
`
const START = { cwd: '/ws', surface: 'terminal', isInteractive: true } as const
const END = (reason: 'clear' | 'resume' | 'prompt_input_exit' | 'logout') =>
  ({ reason, sessionId: 's1', resume: { id: 's1' } }) as never
const SUBMIT = { text: 'hi', origin: { kind: 'composer' } } as never

const attach = ($: Engine, dir: string) =>
  $.tool.call({ tool: 'mcp__workspace__attach', tool_use_id: 'u1', dir })

// The engine's `$` has no state or store reader, so the test's own hooks keep them (a hook above the kit's state bottom mirrors its writes; the store hooks stand for the engine's).
type Mirror = { state: Record<string, unknown>; store: Record<string, unknown> }
const attachedNow = (m: Mirror) => (m.state.attached ?? null) as { name: string; dir: string } | null
const marker = (m: Mirror) => m.store['attached:/ws']

function setup(on: On, extra: Record<string, string> = {}, store: Record<string, unknown> = {}) {
  const mirror: Mirror = { state: {}, store: { ...store } }
  on('state.set', (_$, e, next) => {
    mirror.state[e.key] = e.value
    return next(e)
  })
  on('store.get', (_$, e) => ({ value: mirror.store[e.key] }))
  on('store.set', (_$, e) => {
    mirror.store[e.key] = e.value
    return { value: undefined }
  })
  on('store.delete', (_$, e) => {
    delete mirror.store[e.key]
    return { value: undefined }
  })
  on('store.keys', () => ({ value: Object.keys(mirror.store) }))
  const files: Record<string, string> = { [`${DIR}/CLAUDE.md`]: DOC, ...extra }
  return { files, seen: stubEngine(on, files), m: mirror }
}

test('attach sets state and rejects a non-project dir', async ($, on) => {
  const { m } = setup(on, { '/ws/src/CLAUDE.md': DOC })
  await $.session.start(START)
  expect((await attach($, DIR)).deny).toBeUndefined()
  expect(await attachedNow(m)).toEqual({ name: 'demo', dir: DIR })
  expect((await attach($, '/ws/src')).deny).toMatch(/not a workspace project/)
})

test('attachment survives clear and restores on the next prompt', async ($, on) => {
  const { m } = setup(on)
  await $.session.start(START)
  await attach($, DIR)
  await $.session.end(END('clear'))
  expect(await attachedNow(m)).toBeNull()
  expect(await marker(m)).toEqual({ name: 'demo', dir: DIR })
  await $.prompt.submit(SUBMIT)
  expect((await attachedNow(m))?.name).toBe('demo')
  expect(await marker(m)).toBeUndefined()
})

test('resume and exit drop the attachment', async ($, on) => {
  const { m } = setup(on)
  await $.session.start(START)
  for (const reason of ['resume', 'prompt_input_exit', 'logout'] as const) {
    await attach($, DIR)
    await $.session.end(END(reason))
    expect(await marker(m)).toBeUndefined()
    expect(await attachedNow(m)).toBeNull()
    await $.prompt.submit(SUBMIT)
    expect(await attachedNow(m)).toBeNull()
  }
})

test('session start drops a stale marker', async ($, on) => {
  const { m } = setup(on, {}, { 'attached:/ws': { name: 'demo', dir: DIR } })
  await $.session.start(START)
  await $.prompt.submit(SUBMIT)
  expect(await attachedNow(m)).toBeNull()
})

test('context block present only while attached', async ($, on) => {
  const { m } = setup(on)
  await $.session.start(START)
  const none = await $.prompt.context({ blocks: [] })
  expect(none.blocks.some(b => b.name === 'workspaceProject')).toBe(false)
  await attach($, DIR)
  const some = await $.prompt.context({ blocks: [] })
  const block = some.blocks.find(b => b.name === 'workspaceProject')
  expect(block?.text).toContain('Attached workspace project: demo (feature, active)')
  expect(block?.text).toContain('- design.md — Design')
})

test('context block falls through when the read throws', async ($, on) => {
  const { files, m } = setup(on)
  await $.session.start(START)
  await attach($, DIR)
  delete files[`${DIR}/CLAUDE.md`]
  const r = await $.prompt.context({ blocks: [{ name: 'currentDate', text: 'x' }] })
  expect(r.blocks).toEqual([{ name: 'currentDate', text: 'x' }])
})

test('context after clear restores the project without a submit', async ($, on) => {
  const { m } = setup(on)
  await $.session.start(START)
  await attach($, DIR)
  await $.session.end(END('clear'))
  const r = await $.prompt.context({ blocks: [] })
  expect(r.blocks.some(b => b.name === 'workspaceProject')).toBe(true)
  expect((await attachedNow(m))?.name).toBe('demo')
})

test('a failed restore toasts once and stays detached', async ($, on) => {
  const { files, seen, m } = setup(on)
  await $.session.start(START)
  await attach($, DIR)
  await $.session.end(END('clear'))
  delete files[`${DIR}/CLAUDE.md`]
  await $.prompt.context({ blocks: [] })
  await $.prompt.submit(SUBMIT)
  expect(seen.toasts).toEqual(['workspace: could not restore demo after /clear'])
  expect(await attachedNow(m)).toBeNull()
})
