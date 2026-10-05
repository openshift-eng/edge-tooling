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

// Parses the whole trimmed output as JSON rather than substring-matching
// `"status": "ok"`, so a Bash command whose output merely contains that
// text (accidentally, or via a spoofed echo) cannot arm a note.
function armStatus(text: string | undefined): unknown {
  try {
    return JSON.parse((text ?? '').trim()).status
  } catch {
    return undefined
  }
}

const phase = atom({ plugin: 'next', key: 'phase' } as const, 'idle' as Phase)

export const register: Register = on => {
  on('command.run', { command: SKILL }, async ($, e, next) => {
    await update($, phase, () => 'running')
    return next(e)
  })

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const ran = await next(e)
    if (!ARM.test(e.command) || (await read($, phase)) !== 'running') return ran

    const isArmed = ran.deny === undefined && ran.isError !== true && armStatus(ran.text) === 'ok'
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
      .then(
        () =>
          $.prompt.submit({ text: RESUME }).catch((err: unknown) => {
            $.ui.toast(
              `next: clear ran but resume failed: ${String(err)}. The note is retired to its .consumed.md file — paste it in by hand.`,
            )
          }),
        (err: unknown) => {
          $.ui.toast(`next: clear failed: ${String(err)}. Run /clear by hand.`)
        },
      )

    return done
  })
}
