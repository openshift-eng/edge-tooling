import { expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

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

const ORIGIN = { kind: 'composer' } as const
const PRESENTATION = { isFullscreen: false, columns: 120 }
const PANE = {
  plugin: 'workspace',
  surface: 'terminal',
  component: 'Pane',
  requestId: 'workspace-project',
  props: {
    title: 'demo',
    isFocused: true,
    bodyColumns: 80,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 20 },
    view: {},
  },
} as const

const entry = (name: string, mtimeMs: number, kind: 'file' | 'dir' = 'file') => ({ name, kind, size: 1, mtimeMs, isLink: false })

const TREE = {
  [DIR]: [entry('design.md', 1), entry('scratch.md', 3), entry('CLAUDE.md', 2), entry('.gitignore', 9)],
}
const EXTRA = { [`${DIR}/scratch.md`]: '# Scratch\nnotes', [`${DIR}/design.md`]: 'design' }

test('files list newest first and flags unregistered', async ($, on) => {
  const { m } = setup(on, EXTRA, {}, TREE)
  await $.session.start(START)
  await attach($, DIR)
  const list = m.state.files as { path: string; isRegistered: boolean }[]
  expect(list.map(f => f.path)).toEqual(['scratch.md', 'CLAUDE.md', 'design.md'])
  expect(list.map(f => f.isRegistered)).toEqual([false, true, true])
})

test('pane shows header, progress, current task and the selected file', async ($, on) => {
  setup(on, EXTRA, {}, TREE)
  await $.session.start(START)
  await attach($, DIR)
  await $.tool.call({ tool: 'mcp__workspace__set_task', tool_use_id: 'u3', task: 'Review proposal' })
  const r = await $.command.run({ command: 'project', origin: ORIGIN, presentation: PRESENTATION, args: '' })
  expect(r.text).toBe('Project pane opened.')
  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: /demo · feature · active/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /2\/3/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /Current task: Review/ })).toBeDefined()
  expect((await ui.find({ type: 'Markdown' }))?.text).toBe('# Scratch\nnotes')
  await ui.unmount()
})

test('insert puts the @path in the prompt', async ($, on) => {
  const { seen } = setup(on, EXTRA, {}, TREE)
  await $.session.start(START)
  await attach($, DIR)
  const ui = await $.ui.mount(PANE)
  await ui.press({ key: 'insert' })
  expect(seen.filled).toEqual(['@projects/demo/scratch.md '])
  await ui.unmount()
})

function stubProcess(on: On, ctl: { exit: number; argvs: (readonly string[])[] }, env: Record<string, string> = {}) {
  mock.env(on, env)
  on('process.run', (_$, e) => {
    if (e.argv[0] === 'uname') return { value: { exitCode: 0, stdout: 'Linux\n', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
    ctl.argvs.push(e.argv)
    return { value: { exitCode: ctl.exit, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
}

test('open runs the platform opener detached on the absolute path', async ($, on) => {
  const { seen } = setup(on, EXTRA, {}, TREE)
  const ctl = { exit: 0, argvs: [] as (readonly string[])[] }
  stubProcess(on, ctl)
  await $.session.start(START)
  await attach($, DIR)
  const ui = await $.ui.mount(PANE)
  await ui.press({ key: 'open' })
  expect(ctl.argvs).toEqual([['sh', '-c', '"$1" "$2" >/dev/null 2>&1 &', 'sh', 'xdg-open', `${DIR}/scratch.md`]])
  expect(seen.toasts).toEqual([])
  await ui.unmount()
})

test('open toasts when the launcher fails and the pane keeps working', async ($, on) => {
  const { seen } = setup(on, EXTRA, {}, TREE)
  const ctl = { exit: 3, argvs: [] as (readonly string[])[] }
  stubProcess(on, ctl)
  await $.session.start(START)
  await attach($, DIR)
  const ui = await $.ui.mount(PANE)
  await ui.press({ key: 'open' })
  expect(seen.toasts).toEqual(['workspace: could not open scratch.md'])
  await ui.press({ key: 'insert' })
  expect(seen.filled.length).toBe(1)
  await ui.unmount()
})

test('project command without attachment explains', async ($, on) => {
  const { seen } = setup(on)
  await $.session.start(START)
  const r = await $.command.run({ command: 'project', origin: ORIGIN, presentation: PRESENTATION, args: '' })
  expect(r.text).toMatch(/No workspace project is attached/)
  expect(seen.opened).toEqual([])
})

test('open uses cmd start on Windows', async ($, on) => {
  setup(on, EXTRA, {}, TREE)
  const ctl = { exit: 0, argvs: [] as (readonly string[])[] }
  stubProcess(on, ctl, { OS: 'Windows_NT' })
  await $.session.start(START)
  await attach($, DIR)
  const ui = await $.ui.mount(PANE)
  await ui.press({ key: 'open' })
  expect(ctl.argvs).toEqual([['cmd', '/c', 'start', '', `${DIR}/scratch.md`]])
  await ui.unmount()
})
