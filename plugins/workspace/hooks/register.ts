import type { Register } from 'claude-code'

import { attach, contextBlock, detach, markerKey, onDetach, restoreFromMarker } from './attach'
import { PANE, openPane } from './files'
import { leanIndexRules, parseProjectDoc } from './project'
import { SAVER, consolidatePath, finishSave, boundedSave, isAllowedForSaver, isPlainTarget, isSaverAgent, startSave } from './save'
import { projectPane, setTask } from './ui'
import { bumpTurns, getAttached, getTask } from './state'

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
  set_task: {
    name: 'set_task',
    description:
      'Record what is being worked on right now in the attached workspace project. Call whenever the focus of the work shifts; one short phrase.',
    inputSchema: {
      type: 'object',
      properties: { task: { type: 'string' } },
      required: ['task'],
    },
  },
  save: {
    name: 'save',
    description: 'Save findings, decisions and progress from this conversation to the attached workspace project now.',
  },
  detach: { name: 'detach', description: 'Detach the current workspace project from this session.' },
} as const

const PREFIX = 'mcp__workspace__'

const SAVER_ROLE =
  'You save notes for a workspace project. You are given a project directory and a list of items; write each into the named project file, then end with the one-line report you are asked for.'

async function saverPrompt($: Parameters<typeof startSave>[0]): Promise<string> {
  const skill = `${$.plugin.root}/skills/update-project/SKILL.md`
  try {
    const rules = leanIndexRules(String(await $.fs.read(skill)))
    if (rules.trim()) return `${SAVER_ROLE}\n\n## Lean Index Rules\n${rules}`
  } catch {
    // fall through to the pointer
  }
  return `${SAVER_ROLE}\n\nBefore editing, Read the \`## Lean Index Rules\` section of ${skill} and follow it.`
}

function saveText(o: Awaited<ReturnType<typeof startSave>>, name: string): string {
  switch (o.kind) {
    case 'dispatched':
      return `Saving ${o.items.length} items to ${name} in the background; a toast reports the result.`
    case 'nothing':
      return 'Nothing new to save.'
    case 'failed':
      return `Save failed: ${o.error}`
    case 'skipped':
      return o.why === 'detached' ? 'No workspace project is attached.' : 'A save is already running.'
  }
}

let idleTimer: { cancel: () => void } | undefined
let idleHooked = false

export const register: Register = (on, options) => {
  const opts = { autoSave: options.autoSave !== false }
  const idleMs = (typeof options.idleMinutes === 'number' && options.idleMinutes > 0 ? options.idleMinutes : 50) * 60_000

  on('session.start', async ($, e, next) => {
    try {
      await $.tool.register(TOOLS.attach)
      await $.tool.register(TOOLS.detach)
      await $.tool.register(TOOLS.set_task)
      await $.tool.register(TOOLS.save)
      await $.agent.register({
        name: 'saver',
        description: 'Saves workspace project notes (internal)',
        prompt: await saverPrompt($),
        tools: ['Read', 'Edit', 'Write', 'Bash'],
        maxTurns: 12,
        omitClaudeMd: true,
      })
      await $.command.register({ name: 'project', description: 'Show the attached workspace project' })
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

  on('tool.call', { tool: `${PREFIX}set_task` }, async ($, e) => {
    const r = await setTask($, String(e.task ?? ''))
    return r.ok ? { result: { ok: true }, text: 'Task recorded.' } : { deny: `workspace: ${r.reason}` }
  }).catch(() => ({ deny: 'workspace: set_task failed' }))

  on('tool.call', { tool: `${PREFIX}save` }, async $ => {
    const name = (await getAttached($))?.name ?? ''
    const out = await startSave($, 'manual', { autoSave: true })
    return { result: out, text: saveText(out, name) }
  }).catch(() => ({ deny: 'workspace: save failed' }))

  on('agent.offer', { agent: SAVER }, () => ({ isOffered: false }))

  on('tool.check', async ($, e, next) => {
    try {
      const a = await getAttached($)
      if (a && (await isSaverAgent($, e.agentId)) && isAllowedForSaver(a, e.tool, e.input, consolidatePath($))) {
        const target = (e.input as { file_path?: unknown }).file_path
        if (e.tool === 'Bash' || (typeof target === 'string' && (await isPlainTarget($, a, target)))) {
          return { decision: 'allow' }
        }
      }
    } catch {
      // fall through
    }
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    try {
      if (e.agentId === undefined) {
        await bumpTurns($)
        idleTimer?.cancel()
        idleTimer = $.clock.after(idleMs, () => void startSave($, 'idle', opts))
        if (!idleHooked) {
          idleHooked = true
          onDetach.push(() => idleTimer?.cancel())
        }
      } else if (await isSaverAgent($, e.agentId)) {
        await finishSave($, e.answer)
      }
    } catch {
      // fail open
    }
    return next(e)
  })

  on('session.compact', async ($, e, next) => {
    try {
      if (e.agentId === undefined && e.trigger !== 'precompute' && (await getAttached($))) {
        await boundedSave($, 'compact', opts)
      }
    } catch {
      // fail open
    }
    return next(e)
  })

  on('command.run', { command: 'next:go' }, async ($, e, next) => {
    try {
      await boundedSave($, 'handoff', opts)
    } catch {
      // fail open
    }
    return next(e)
  })

  on('command.run', { command: 'project' }, $ => openPane($)).catch(() => ({ text: 'workspace: could not open the project pane' }))

  on('ui.render', { component: 'Pane', requestId: PANE }, ($, e) => projectPane($, e)).catch(($, e, next) => next(e))

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
