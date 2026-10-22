import { expect, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

import { applyPrompt, detectPrompt, gate, isAllowedForSaver, parseDetected } from '../hooks/save'
import { parseProjectDoc } from '../hooks/project'
import { DIR, DOC, START, attach, setup } from './harness'
import type { Seen } from './engine'

const ENTRY = (name: string) => ({ name, kind: 'file' as const, size: 1, mtimeMs: 1, isLink: false })
const USAGE = { input_tokens: 5, output_tokens: 1, cache_read_input_tokens: 90, cache_creation_input_tokens: 0 }
const A = { name: 'demo', dir: DIR }
const CONSOLIDATE = '/p/scripts/consolidate-project.py'

// The engine has no model: `model.fork` and `agent.spawn` are answered per test.
function stubModel(on: On, seen: Seen, ctl: { detect: string | null }) {
  on('model.fork', (_$, e) => {
    seen.forks.push(e.prompt)
    if (ctl.detect === null) return { value: { isAnswered: false, reason: 'nothing-to-fork' } } as never
    return { value: { isAnswered: true, text: ctl.detect, usage: USAGE } } as never
  })
  on('agent.spawn', (_$, e) => {
    seen.spawns.push(e.prompt)
    seen.agents = [{ id: 'a1', description: 'saver', type: 'workspace:saver', status: 'running' }]
    return { model: 'haiku' } as never
  })
}

// Only the plugin's own hooks may read its state, so a save starts through its `save` tool.
const save = ($: Engine, id = 'u') => $.tool.call({ tool: 'mcp__workspace__save', tool_use_id: id })
const turn = ($: Engine, answer: string, agentId?: string) =>
  $.turn.complete({ answer, durationMs: 1, isAborted: false, turnId: 't', reason: 'answer', agentId })
const saverAnswer = ($: Engine, answer: string) => turn($, answer, 'a1')
const check = async ($: Engine, tool: string, input: unknown, agentId?: string) =>
  (await $.tool.check({ tool, input, tool_use_id: 'c1', agentId })).decision
// The plugin's root is not on the test's `$`; the saver prompt carries the exact command.
const consolidate = (seen: Seen) => (seen.spawns[0] ?? '').match(/python3 "[^"]+" demo/)?.[0] ?? ''

async function saverSession($: Engine, on: On) {
  const { seen } = setup(on)
  stubModel(on, seen, { detect: '- design.md: x' })
  await $.session.start(START)
  await attach($, DIR)
  await save($)
  return seen
}

test('nothing detected means no spawn, toast, or write', async ($, on) => {
  const { seen } = setup(on)
  stubModel(on, seen, { detect: ' nothing ' })
  await $.session.start(START)
  await attach($, DIR)
  const before = seen.writes.length
  const r = await save($)
  expect(r.result).toEqual({ kind: 'nothing' })
  expect(r.text).toBe('Nothing new to save.')
  expect(seen.spawns).toEqual([])
  expect(seen.toasts).toEqual([])
  expect(seen.writes.length).toBe(before)
})

test('detected items dispatch the saver and toast its report', async ($, on) => {
  const { seen, m } = setup(on)
  stubModel(on, seen, { detect: '- design.md: chose prompt.context' })
  await $.session.start(START)
  await attach($, DIR)
  const r = await save($)
  expect(r.result).toEqual({ kind: 'dispatched', items: ['design.md: chose prompt.context'] })
  expect(r.text).toBe('Saving 1 items to demo in the background; a toast reports the result.')
  expect(seen.spawns[0]).toContain('design.md: chose prompt.context')
  expect(seen.spawns[0]).toContain('consolidate-project.py')
  expect(m.state.savedAt ?? null).toBeNull()
  await saverAnswer($, 'did things\nupdated: 1 finding to design.md')
  expect(seen.toasts).toEqual(['workspace: saved — 1 finding to design.md'])
  expect(typeof m.state.savedAt).toBe('number')
})

test('saver failure toasts once', async ($, on) => {
  const { seen } = setup(on)
  stubModel(on, seen, { detect: '- design.md: x' })
  await $.session.start(START)
  await attach($, DIR)
  await save($)
  await saverAnswer($, 'failed: disk full')
  expect(seen.toasts.length).toBe(1)
  expect(seen.toasts[0]).toMatch(/save failed — disk full/)
})

test('a saver reporting nothing stays silent and frees the slot', async ($, on) => {
  const { seen } = setup(on)
  stubModel(on, seen, { detect: '- design.md: x' })
  await $.session.start(START)
  await attach($, DIR)
  await save($)
  await saverAnswer($, 'nothing')
  expect(seen.toasts).toEqual([])
  expect(((await save($)).result as { kind: string }).kind).toBe('dispatched')
})

test('no project attached explains and forks nothing', async ($, on) => {
  const { seen } = setup(on)
  stubModel(on, seen, { detect: 'NOTHING' })
  await $.session.start(START)
  const r = await save($)
  expect(r.text).toBe('No workspace project is attached.')
  expect(seen.forks).toEqual([])
})

test('a second save while one is in flight is skipped', async ($, on) => {
  const { seen } = setup(on)
  stubModel(on, seen, { detect: '- design.md: x' })
  await $.session.start(START)
  await attach($, DIR)
  await save($)
  expect((await save($)).result).toEqual({ kind: 'skipped', why: 'in-flight' })
  expect(seen.spawns.length).toBe(1)
})

test('concurrent triggers produce one save', async ($, on) => {
  const { seen } = setup(on)
  stubModel(on, seen, { detect: '- design.md: x' })
  await $.session.start(START)
  await attach($, DIR)
  const rs = await Promise.all([save($, 'u1'), save($, 'u2'), save($, 'u3')])
  const kinds = rs.map(r => (r.result as { kind: string }).kind).sort()
  expect(kinds).toEqual(['dispatched', 'skipped', 'skipped'])
  expect(seen.forks.length).toBe(1)
  expect(seen.spawns.length).toBe(1)
})

test('a failed fork reports and frees the slot', async ($, on) => {
  const { seen } = setup(on)
  stubModel(on, seen, { detect: null })
  await $.session.start(START)
  await attach($, DIR)
  expect((await save($)).text).toBe('Save failed: nothing-to-fork')
  expect((await save($)).text).toBe('Save failed: nothing-to-fork')
})

test('already saved items are passed to the next detect', async ($, on) => {
  const { seen } = setup(on)
  stubModel(on, seen, { detect: '- design.md: first item' })
  await $.session.start(START)
  await attach($, DIR)
  await save($)
  await saverAnswer($, 'updated: x')
  await save($)
  expect(seen.forks[1]).toContain('Already saved this session:')
  expect(seen.forks[1]).toContain('first item')
  expect(seen.forks[0]).not.toContain('first item')
})

test('detach clears the in-flight slot and the already-saved list', async ($, on) => {
  const { seen } = setup(on)
  stubModel(on, seen, { detect: '- design.md: first item' })
  await $.session.start(START)
  await attach($, DIR)
  await save($)
  await $.tool.call({ tool: 'mcp__workspace__detach', tool_use_id: 'u2' })
  await attach($, DIR)
  expect(((await save($)).result as { kind: string }).kind).toBe('dispatched')
  expect(seen.forks[1]).not.toContain('first item')
})

test('after a save the selected file preview is reloaded and touched files are marked', async ($, on) => {
  const { seen, files, m } = setup(on, { [`${DIR}/design.md`]: 'old' }, {}, { [DIR]: [ENTRY('design.md'), ENTRY('notes.md')] })
  stubModel(on, seen, { detect: '- design.md: x' })
  await $.session.start(START)
  await attach($, DIR)
  expect(m.state.selected).toBe('design.md')
  expect(m.state.preview).toBe('old')
  await save($)
  files[`${DIR}/design.md`] = 'new text'
  await saverAnswer($, 'updated: 1 finding to design.md')
  expect(m.state.preview).toBe('new text')
  expect(m.state.marks).toEqual({ 'design.md': 'updated' })
})

test('the detect fork usage is logged at debug level', async ($, on) => {
  const { seen } = setup(on)
  stubModel(on, seen, { detect: 'NOTHING' })
  await $.session.start(START)
  await attach($, DIR)
  await save($)
  expect(seen.logs).toContainEqual({ text: 'workspace: detect trigger=manual cache_read=90 input=5', to: 'debug' })
})

test('auto-allow covers markdown under the project only', async ($, on) => {
  const seen = await saverSession($, on)
  expect(await check($, 'Edit', { file_path: `${DIR}/design.md` }, 'a1')).toBe('allow')
  expect(await check($, 'Write', { file_path: `${DIR}/notes/a.md` }, 'a1')).toBe('allow')
  expect(await check($, 'Bash', { command: consolidate(seen) }, 'a1')).toBe('allow')
})

test('auto-allow rejects paths outside the project or not markdown', async ($, on) => {
  const seen = await saverSession($, on)
  for (const file_path of [
    `${DIR}/../../repos/x/README.md`,
    '/ws/projects/demo-other/a.md',
    `${DIR}/a.md.sh`,
    'design.md',
  ]) {
    expect(await check($, 'Edit', { file_path }, 'a1')).toBe('ask')
  }
  expect(await check($, 'Bash', { command: 'rm -rf /ws' }, 'a1')).toBe('ask')
  expect(await check($, 'Bash', { command: `${consolidate(seen)}; rm -rf /ws` }, 'a1')).toBe('ask')
  expect(await check($, 'Edit', { file_path: `${DIR}/design.md` })).toBe('ask')
  expect(await check($, 'Edit', { file_path: `${DIR}/design.md` }, 'other')).toBe('ask')
})

test('auto-allow falls through when its own code throws', async ($, on) => {
  await saverSession($, on)
  expect(await check($, 'Edit', null, 'a1')).toBe('ask')
  expect(await check($, 'Bash', undefined, 'a1')).toBe('ask')
})

test('a non-saver agent gets no auto-allow and its turns are ignored', async ($, on) => {
  const { seen } = setup(on)
  stubModel(on, seen, { detect: '- design.md: x' })
  await $.session.start(START)
  await attach($, DIR)
  await save($)
  seen.agents.push({ id: 'b2', description: 'x', type: 'general-purpose', status: 'running' })
  expect(await check($, 'Edit', { file_path: `${DIR}/design.md` }, 'b2')).toBe('ask')
  await turn($, 'updated: nope', 'b2')
  expect(seen.toasts).toEqual([])
})

test('isAllowedForSaver resolves dot segments before comparing', () => {
  const ok = (tool: string, input: unknown) => isAllowedForSaver(A, tool, input, CONSOLIDATE)
  expect(ok('Edit', { file_path: `${DIR}/design.md` })).toBe(true)
  expect(ok('Edit', { file_path: `${DIR}/./sub/../design.md` })).toBe(true)
  expect(ok('Edit', { file_path: '/ws/projects/demo/../../repos/x/README.md' })).toBe(false)
  expect(ok('Edit', { file_path: '/ws/projects/demo-other/a.md' })).toBe(false)
  expect(ok('Edit', { file_path: '/ws/projects/demo/a.md.sh' })).toBe(false)
  expect(ok('Edit', { file_path: '/ws/projects/demo' })).toBe(false)
  expect(ok('Edit', null)).toBe(false)
  expect(ok('Write', { file_path: 42 })).toBe(false)
  expect(ok('Read', { file_path: `${DIR}/design.md` })).toBe(false)
  expect(ok('Bash', { command: `python3 "${CONSOLIDATE}" demo` })).toBe(true)
  expect(ok('Bash', { command: `python3 "${CONSOLIDATE}" other` })).toBe(false)
  expect(ok('Bash', { command: `python3 "${CONSOLIDATE}" demo && id` })).toBe(false)
})

test('gate order: detached, disabled, in-flight, no-new-turns; manual skips the last two checks that apply to auto', () => {
  const s = { isAttached: true, inFlight: false, turnsSinceSave: 1 }
  expect(gate('idle', true, { ...s, isAttached: false })).toBe('detached')
  expect(gate('manual', false, { ...s, isAttached: false })).toBe('detached')
  expect(gate('idle', false, s)).toBe('disabled')
  expect(gate('manual', false, s)).toBeNull()
  expect(gate('idle', true, { ...s, inFlight: true })).toBe('in-flight')
  expect(gate('manual', true, { ...s, inFlight: true })).toBe('in-flight')
  expect(gate('idle', true, { ...s, turnsSinceSave: 0 })).toBe('no-new-turns')
  expect(gate('manual', true, { ...s, turnsSinceSave: 0 })).toBeNull()
  expect(gate('end', true, s)).toBeNull()
})

test('detect answers parse to items or null', () => {
  expect(parseDetected(' nothing \n')).toBeNull()
  expect(parseDetected('no findings')).toBeNull()
  expect(parseDetected('Here:\n- design.md: a\n-  CLAUDE.md: b\ntext')).toEqual(['design.md: a', 'CLAUDE.md: b'])
})

test('prompts carry the project context and the apply constraints', () => {
  const p = detectPrompt(parseProjectDoc(DOC), DOC, ['design.md: old'])
  expect(p).toContain('Answer exactly `NOTHING`')
  expect(p).toContain('| [design.md](design.md) | Design |')
  expect(p).toContain('Already saved this session:\n- design.md: old')
  const q = applyPrompt(A, ['design.md: x'], CONSOLIDATE)
  expect(q).toContain(`Edit only \`*.md\` files under \`${DIR}\`. Never change \`status:\`. Update \`last-active:\` to now`)
  expect(q).toContain(`run exactly \`python3 "${CONSOLIDATE}" demo\` first`)
  expect(q).toContain('`updated: <summary>`, `nothing`, or `failed: <error>`')
})
