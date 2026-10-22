import type { On } from 'claude-code'

export type Seen = {
  toasts: string[]
  writes: { path: string; text: string }[]
  status: (string | undefined)[]
  forks: string[]
  spawns: string[]
  opened: unknown[]
  filled: string[]
  logs: { text: string; to: string }[]
  agents: { id: string; description: string; type: string; status: string }[]
  links: Record<string, string>
  // Paths whose stat rejects with the given message, like a missing or unreadable one.
  statErrors: Record<string, string>
}

export type Entry = { name: string; kind: 'file' | 'dir' | 'other'; size: number; mtimeMs: number; isLink: boolean }

const SKILL = '# update-project\n\n## Lean Index Rules\n\n- one line per file\n\n## Other\n'

// The hooks beneath the plugin stand for the engine. `files` is live: a test
// deletes a key to make `fs.read` throw.
export function stubEngine(on: On, files: Record<string, string>, tree: Record<string, Entry[]> = {}): Seen {
  const seen: Seen = { toasts: [], writes: [], status: [], forks: [], spawns: [], opened: [], filled: [], logs: [], agents: [], links: {}, statErrors: {} }
  on('fs.read', (_$, e) => {
    if (e.path.endsWith('skills/update-project/SKILL.md')) return { value: SKILL }
    const text = files[e.path]
    if (text === undefined) throw new Error(`ENOENT: ${e.path}`)
    return { value: text }
  })
  on('fs.exists', (_$, e) => ({ value: e.path in files }))
  on('fs.stat', (_$, e) => {
    if (e.path in seen.statErrors) return { deny: seen.statErrors[e.path] }
    const target = seen.links[e.path]
    return {
      value: { kind: 'file', size: (files[e.path] ?? '').length, mtimeMs: 0, isLink: target !== undefined, realPath: target ?? e.path },
    }
  })
  on('fs.list', (_$, e) => ({ value: tree[e.path ?? ''] ?? [] }))
  on('fs.write', (_$, e) => {
    seen.writes.push({ path: e.path, text: e.text })
    files[e.path] = e.text
    return { value: undefined }
  })
  on('ui.toast', (_$, e) => {
    seen.toasts.push(e.text)
    return { value: undefined }
  })
  on('ui.status', (_$, e) => {
    seen.status.push(e.text)
    return { value: undefined }
  })
  on('ui.log', (_$, e) => {
    seen.logs.push({ text: e.text, to: e.to })
    return { value: undefined }
  })
  on('ui.open', (_$, e) => {
    seen.opened.push(e)
    return { value: { isPlaced: true } }
  })
  on('prompt.fill', (_$, e) => {
    seen.filled.push(e.text)
    return { isFilled: true } as never
  })
  on('tool.register', (_$, e) => ({ value: { tool: `mcp__workspace__${e.name}` } }))
  on('agent.register', (_$, e) => ({ value: { agent: `workspace:${e.name}` } }))
  on('command.register', (_$, e) => ({ value: { command: `workspace:${e.name}` } }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('session.root', () => ({ value: '/ws' }))
  on('session.cwd', () => ({ value: '/ws' }))
  on('session.end', (_$, e) => ({ sessionId: e.sessionId }))
  on('prompt.submit', (_$, e) => ({ text: e.text }))
  on('prompt.context', (_$, e) => ({ blocks: e.blocks }))
  on('agent.list', () => ({ value: seen.agents as never }))
  on('tool.check', () => ({ decision: 'ask' }))
  on('turn.complete', (_$, e) => ({ text: e.answer }))
  return seen
}
