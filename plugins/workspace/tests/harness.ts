import { mock } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

import { stubEngine } from './engine'
import type { Entry } from './engine'

export const DIR = '/ws/projects/demo'
export const DOC = `---
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
- [x] one
- [x] two
- [ ] three
`
export const START = { cwd: '/ws', surface: 'terminal', isInteractive: true } as const
export const END = (reason: 'clear' | 'resume' | 'prompt_input_exit' | 'logout') =>
  ({ reason, sessionId: 's1', resume: { id: 's1' } }) as never
export const SUBMIT = { text: 'hi', origin: { kind: 'composer' } } as never

export const attach = ($: Engine, dir: string) =>
  $.tool.call({ tool: 'mcp__workspace__attach', tool_use_id: 'u1', dir })

// The engine's `$` has no state or store reader, so the test's own hooks keep them (a hook above the kit's state bottom mirrors its writes; the store hooks stand for the engine's).
export type Mirror = { state: Record<string, unknown>; store: Record<string, unknown> }
export const attachedNow = (m: Mirror) => (m.state.attached ?? null) as { name: string; dir: string } | null
export const marker = (m: Mirror) => m.store['attached:/ws']

export function setup(on: On, extra: Record<string, string> = {}, store: Record<string, unknown> = {}, tree: Record<string, Entry[]> = {}) {
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
  const clock = mock.clock(on)
  const files: Record<string, string> = { [`${DIR}/CLAUDE.md`]: DOC, ...extra }
  return { files, seen: stubEngine(on, files, tree), m: mirror, clock }
}

