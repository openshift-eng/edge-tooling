import { refreshFiles } from './files'
import type { ProjectDoc } from './project'
import { parseProjectDoc } from './project'
import { clearAttached, getAttached, getTask, setAttached } from './state'
import type { Api, Attached } from './state'
import { refreshStatus, startStatusTimer } from './ui'

export const MARKER = 'attached'
export const markerKey = (root: string) => `${MARKER}:${root}`

type AttachResult = { ok: true; name: string } | { ok: false; reason: string }

// Cleanups that run on detach (the idle-timer cancel, the save slot reset).
export const onDetach: Array<() => void> = []

const basename = (p: string) => p.replace(/\/+$/, '').split('/').pop() ?? ''
const dirname = (p: string) => p.replace(/\/+$/, '').split('/').slice(0, -1).join('/')

export async function attach($: Api, dir: string): Promise<AttachResult> {
  const clean = dir.replace(/\/+$/, '')
  if (basename(dirname(clean)) !== 'projects') {
    return { ok: false, reason: `${dir} is not a workspace project (parent directory must be "projects")` }
  }
  if (!/^[A-Za-z0-9._-]+$/.test(basename(clean))) {
    return { ok: false, reason: 'Project directory name contains unsafe characters' }
  }
  if (!(await $.fs.exists(`${clean}/CLAUDE.md`))) {
    return { ok: false, reason: `${clean}/CLAUDE.md not found` }
  }
  const doc = parseProjectDoc(String(await $.fs.read(`${clean}/CLAUDE.md`)))
  const name = basename(clean)
  await setAttached($, { name, dir: clean }, doc.frontmatter['current-task'] ?? '')
  await refreshFiles($).catch(() => {})
  await refreshStatus($)
  startStatusTimer($)
  return { ok: true, name }
}

export async function detach($: Api): Promise<void> {
  for (const cancel of onDetach) cancel()
  await clearAttached($)
  $.ui.status(undefined)
}

export function contextBlock(a: Attached, doc: ProjectDoc, currentTask: string) {
  const fm = doc.frontmatter
  const lines = [`Attached workspace project: ${a.name} (${fm.type ?? 'unknown'}, ${fm.status ?? 'unknown'})`, `Path: ${a.dir}`]
  if (fm.branch) lines.push(`Branch: ${fm.branch}`)
  if (fm.worktree_path) lines.push(`Worktree: ${fm.worktree_path}`)
  if (currentTask) lines.push(`Current task: ${currentTask}`)
  lines.push('Files:', ...doc.refFiles.map(f => `- ${f.path} — ${f.description}`))
  lines.push(`Keep notes under ${a.dir}. Call mcp__workspace__set_task when the focus of the work changes.`)
  return { name: 'workspaceProject' as const, text: lines.join('\n') }
}


// Idempotent: the marker is deleted before attaching, so a second caller
// (prompt.context may fire before prompt.submit) finds nothing and cannot
// toast twice.
let restoring: Promise<void> | null = null
export function restoreFromMarker($: Api): Promise<void> {
  restoring ??= (async () => {
    if (await getAttached($)) return
    const root = await $.session.root()
    const marker = (await $.store.get(markerKey(root))) as Attached | undefined
    if (!marker) return
    await $.store.delete(markerKey(root))
    const r = await attach($, marker.dir)
    if (!r.ok) $.ui.toast(`workspace: could not restore ${marker.name} after /clear`)
  })().finally(() => {
    restoring = null
  })
  return restoring
}
