import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { Phase } from '../types'

const SKILL = 'next:go'
// The first line is for the person: the /clear itself prints nothing.
const RESUME =
  'Starting from a new session: the previous context was cleared.\n\n' +
  'Start the "Next task" from the handoff note loaded at session start now. ' +
  'Do not ask for confirmation first.'

// `python3 ".../handoff.py" arm --project-dir ...`
const ARM = /handoff\.py["']?\s+arm\b/
const OK = /"status"\s*:\s*"ok"/

const phase = atom({ plugin: 'next', key: 'phase' } as const, 'idle' as Phase)

export const register: Register = on => {
  on('command.run', { command: SKILL }, async ($, e, next) => {
    await update($, phase, () => 'running')
    return next(e)
  })

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const ran = await next(e)
    if (!ARM.test(e.command) || (await read($, phase)) !== 'running') return ran

    const isArmed = ran.deny === undefined && ran.isError !== true && OK.test(ran.text ?? '')
    if (isArmed) await update($, phase, () => 'armed')

    return ran
  })

  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    if (e.agentId !== undefined) return done

    const now = await read($, phase)
    if (now === 'idle') return done

    await update($, phase, () => 'idle')
    if (now !== 'armed' || e.reason !== 'answer') {
      $.ui.toast('next: the handoff was not armed. The session stays as it is.')
      return done
    }

    $.ui.toast('next: handoff armed. Clearing and resuming…')
    // Queued: both run once the session is idle, after this turn ends.
    void $.command
      .run({ command: 'clear' })
      .then(() => $.prompt.submit({ text: RESUME }))
      .catch((err: unknown) => {
        $.ui.toast(`next: clear or resume failed: ${String(err)}. Run /clear by hand.`)
      })

    return done
  })
}
