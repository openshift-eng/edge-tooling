import { expect, test } from 'claude-code/testing'

import { DIR, DOC, END, SUBMIT, START, attach, attachedNow, marker, setup } from './harness'

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

test('attach rejects a project directory name with shell metacharacters', async ($, on) => {
  const { m, files } = setup(on)
  for (const name of ['demo$(id)', 'demo;x', 'de mo', 'demo`x`']) {
    files[`/ws/projects/${name}/CLAUDE.md`] = DOC
    const r = await attach($, `/ws/projects/${name}`)
    expect(r.deny).toMatch(/unsafe characters/)
  }
  expect(await attachedNow(m)).toBeNull()
})
