import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

const ORIGIN = { kind: 'composer' } as const
const PRESENTATION = { isFullscreen: false, columns: 120 }
const ARM_CMD = 'python3 "/p/scripts/handoff.py" arm --project-dir "/repo"'

type Seen = { commands: string[]; prompts: string[]; toasts: string[] }

// The test's hooks stand for the engine beneath the mod.
function engine(on: On, armOutput: string): Seen {
  const seen: Seen = { commands: [], prompts: [], toasts: [] }
  on('ui.toast', (_$, e) => {
    seen.toasts.push(String((e as { text?: string }).text ?? JSON.stringify(e)))
    return { value: undefined }
  })
  on('command.run', (_$, e) => {
    seen.commands.push(e.args ? `${e.command} ${e.args}` : e.command)
    return { text: '' }
  })
  on('prompt.submit', (_$, e) => {
    seen.prompts.push(e.text)
    return { text: e.text }
  })
  on('tool.call', { tool: 'Bash' }, () => ({
    result: { stdout: armOutput, stderr: '', interrupted: false },
    text: armOutput,
  }))
  on('turn.complete', (_$, e) => ({ text: e.answer }))
  return seen
}

async function go($: Engine, args = 'fix the e2e') {
  await $.command.run({ command: 'next:go', args, origin: ORIGIN, presentation: PRESENTATION })
}

async function endTurn($: Engine, reason: 'answer' | 'aborted' = 'answer') {
  await $.turn.complete({
    answer: 'Handoff armed.',
    durationMs: 10,
    isAborted: reason === 'aborted',
    turnId: 't1',
    reason,
  })
}

test('an armed handoff clears and resumes', async ($, on) => {
  const seen = engine(on, '{"status": "ok", "expires_in": "1h"}')
  const clock = mock.clock(on)
  await go($)
  await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: ARM_CMD })
  await endTurn($)
  await clock.settle()

  expect(seen.toasts).toEqual(['next: handoff armed. Clearing and resuming…'])
  expect(seen.commands).toEqual(['next:go fix the e2e', 'clear'])
  expect(seen.prompts.length).toBe(1)
  expect(seen.prompts[0]).toMatch(/^Starting from a new session/)
  expect(seen.prompts[0]).toMatch(/Do not ask for confirmation/)
})

test('a failed arm keeps the session', async ($, on) => {
  const seen = engine(on, '{"status": "error", "message": "too long"}')
  await go($)
  await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: ARM_CMD })
  await endTurn($)

  expect(seen.commands).toEqual(['next:go fix the e2e'])
  expect(seen.prompts).toEqual([])
  expect(seen.toasts[0]).toMatch(/not armed/)
})

test('an interrupted turn keeps the session', async ($, on) => {
  const seen = engine(on, '{"status": "ok"}')
  await go($)
  await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: ARM_CMD })
  await endTurn($, 'aborted')

  expect(seen.commands).toEqual(['next:go fix the e2e'])
})

test('an arm outside /next:go does not clear', async ($, on) => {
  const seen = engine(on, '{"status": "ok"}')
  await $.tool.call({ tool: 'Bash', tool_use_id: 'u1', command: ARM_CMD })
  await endTurn($)

  expect(seen.commands).toEqual([])
  expect(seen.prompts).toEqual([])
})
