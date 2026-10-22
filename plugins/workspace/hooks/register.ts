import type { Register } from 'claude-code'

import { attach, contextBlock, detach, markerKey, restoreFromMarker } from './attach'
import { parseProjectDoc } from './project'
import { getAttached, getTask } from './state'

const TOOLS = {
  attach: {
    name: 'attach',
    description:
      "Attach a workspace project to this session. Call as the last step of /workspace:new-project and /workspace:resume-project with the project's absolute directory.",
    inputSchema: {
      type: 'object',
      properties: { dir: { type: 'string' } },
      required: ['dir'],
    },
  },
  detach: { name: 'detach', description: 'Detach the current workspace project from this session.' },
} as const

const PREFIX = 'mcp__workspace__'

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    try {
      await $.tool.register(TOOLS.attach)
      await $.tool.register(TOOLS.detach)
      // A fresh process never attaches.
      await $.store.delete(markerKey(await $.session.root()))
    } catch {
      // fail open
    }
    return next(e)
  })

  on('tool.call', { tool: `${PREFIX}attach` }, async ($, e) => {
    const r = await attach($, String(e.dir ?? ''))
    return r.ok
      ? { result: { ok: true, name: r.name }, text: `Attached ${r.name}.` }
      : { deny: `workspace: ${r.reason}` }
  }).catch(() => ({ deny: 'workspace: attach failed' }))

  on('tool.call', { tool: `${PREFIX}detach` }, async $ => {
    await detach($)
    return { result: { ok: true }, text: 'Detached.' }
  }).catch(() => ({ deny: 'workspace: detach failed' }))

  on('tool.check', { tool: /^mcp__workspace__/ }, async ($, e, next) => {
    const r = await next(e)
    return e.agentId === undefined ? { decision: 'allow' } : r
  }).catch(($, e, next) => next(e))

  on('session.end', async ($, e, next) => {
    try {
      const root = await $.session.root()
      const a = await getAttached($)
      if (e.reason === 'clear' && a) await $.store.set(markerKey(root), a)
      else await $.store.delete(markerKey(root))
      await detach($)
    } catch {
      // fail open
    }
    return next(e)
  })

  on('prompt.submit', async ($, e, next) => {
    try {
      await restoreFromMarker($)
    } catch {
      // fail open
    }
    return next(e)
  }).catch(($, e, next) => next(e))

  on('prompt.context', async ($, e, next) => {
    const r = await next(e)
    try {
      await restoreFromMarker($)
      const a = await getAttached($)
      if (!a) return r
      const doc = parseProjectDoc(String(await $.fs.read(`${a.dir}/CLAUDE.md`)))
      return { ...r, blocks: [...r.blocks, contextBlock(a, doc, await getTask($))] }
    } catch {
      return r
    }
  })
}
