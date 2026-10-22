import { expect, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

import { DIR, END, START, attach, setup } from './harness'

const USAGE = { input_tokens: 5, output_tokens: 1, cache_read_input_tokens: 90, cache_creation_input_tokens: 0 }

type Ctl = { order: string[]; fork: 'items' | 'throw' | 'hang' }

// The engine has no model, compaction or `next`: each is answered here and
// records the order the plugin's hooks let calls through.
function stubs(on: On, seen: ReturnType<typeof setup>['seen'], ctl: Ctl) {
  on('model.fork', (_$, e) => {
    ctl.order.push('fork')
    seen.forks.push(e.prompt)
    if (ctl.fork === 'throw') throw new Error('fork down')
    if (ctl.fork === 'hang') return new Promise(() => {}) as never
    return { value: { isAnswered: true, text: '- design.md: x', usage: USAGE } } as never
  })
  on('agent.spawn', () => {
    ctl.order.push('spawn')
    return { model: 'haiku' } as never
  })
  on('session.compact', () => {
    ctl.order.push('compact')
    return { skip: 'test' } as never
  })
  on('command.run', { command: 'next:go' }, () => {
    ctl.order.push('command:next:go')
    return { text: 'went' }
  })
}

const turn = ($: Engine) =>
  $.turn.complete({ answer: 'a', durationMs: 1, isAborted: false, turnId: 't', reason: 'answer' })

const compact = ($: Engine, trigger: 'plugin' | 'precompute' = 'plugin') => $.session.compact({ trigger, messages: [] })

async function session($: Engine, on: On, ctl: Partial<Ctl> = {}) {
  const s = setup(on)
  const c: Ctl = { order: [], fork: 'items', ...ctl }
  stubs(on, s.seen, c)
  await $.session.start(START)
  await attach($, DIR)
  return { ...s, c }
}

test('compaction waits for detection before proceeding', async ($, on) => {
  const { c } = await session($, on)
  await turn($)
  await compact($)
  expect(c.order.indexOf('fork')).toBeGreaterThanOrEqual(0)
  expect(c.order.indexOf('fork')).toBeLessThan(c.order.indexOf('compact'))
})

test('precompute compaction does not save', async ($, on) => {
  const { seen } = await session($, on)
  await turn($)
  await compact($, 'precompute')
  expect(seen.forks).toEqual([])
})

test('handoff save dispatches before next:go proceeds', async ($, on) => {
  const { c } = await session($, on)
  await turn($)
  await $.command.run({ command: 'next:go', args: '' } as never)
  expect(c.order).toEqual(['fork', 'spawn', 'command:next:go'])
})

test('idle fires after the threshold and only after a new turn', async ($, on) => {
  const { seen, clock } = await session($, on)
  await turn($)
  await clock.advance(49 * 60_000)
  expect(seen.forks.length).toBe(0)
  await clock.advance(60_000)
  expect(seen.forks.length).toBe(1)
  await clock.advance(50 * 60_000)
  expect(seen.forks.length).toBe(1)
})

test('autoSave false suppresses every trigger', { options: { autoSave: false } }, async ($, on) => {
  const { seen, clock } = await session($, on)
  await turn($)
  await compact($)
  await $.command.run({ command: 'next:go', args: '' } as never)
  await clock.advance(60 * 60_000)
  expect(seen.forks.length).toBe(0)
})

test('the save tool still forks with autoSave false', { options: { autoSave: false } }, async ($, on) => {
  const { seen } = await session($, on)
  await $.tool.call({ tool: 'mcp__workspace__save', tool_use_id: 'u' })
  expect(seen.forks.length).toBe(1)
})

test('detach cancels the idle timer', async ($, on) => {
  const { seen, clock } = await session($, on)
  await turn($)
  await $.tool.call({ tool: 'mcp__workspace__detach', tool_use_id: 'd' })
  await clock.advance(60 * 60_000)
  expect(seen.forks.length).toBe(0)
})

test('compaction proceeds when the save throws', async ($, on) => {
  const { c } = await session($, on, { fork: 'throw' })
  await turn($)
  await compact($)
  expect(c.order).toContain('compact')
})

test('session end never forks and never blocks', async ($, on) => {
  const { seen, m } = await session($, on, { fork: 'hang' })
  await turn($)
  await $.session.end(END('clear'))
  expect(seen.forks.length).toBe(0)
  expect(m.store['attached:/ws']).toEqual({ name: 'demo', dir: DIR })
})
