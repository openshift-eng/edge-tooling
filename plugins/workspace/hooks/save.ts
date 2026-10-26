import { refreshFiles } from './files'
import { parseProjectDoc } from './project'
import type { ProjectDoc } from './project'
import { addMarks, getAttached, getFiles, getTurnsSinceSave, resetTurns, setSavedAt } from './state'
import type { Api, Attached } from './state'
import { onDetach } from './attach'
import { refreshStatus } from './ui'

export type Trigger = 'compact' | 'handoff' | 'idle' | 'manual'
export type SaveOutcome =
  | { kind: 'skipped'; why: 'detached' | 'no-new-turns' | 'disabled' | 'in-flight' }
  | { kind: 'nothing' }
  | { kind: 'dispatched'; items: string[]; agentId?: string }
  | { kind: 'failed'; error: string }

export const SAVER = 'workspace:saver'

const STALE_MS = 5 * 60_000
const BOUND_MS = 8_000

let alreadySaved: string[] = []
let inFlight = false
let staleTimer: { cancel: () => void } | undefined

function release() {
  staleTimer?.cancel()
  staleTimer = undefined
  inFlight = false
}

onDetach.push(() => {
  alreadySaved = []
  release()
})

// A hook that waits on a save must never be held by a hung model call.
export async function boundedSave($: Api, trigger: Trigger, opts: { autoSave: boolean }, ms = BOUND_MS): Promise<void> {
  let timer: { cancel: () => void } | undefined
  const expired = new Promise<void>(resolve => {
    timer = $.clock.after(ms, resolve)
  })
  try {
    await Promise.race([startSave($, trigger, opts), expired])
  } finally {
    timer?.cancel()
  }
}

export async function isSaverAgent($: Api, agentId: string | undefined): Promise<boolean> {
  if (agentId === undefined) return false
  const info = (await $.agent.list()).find(a => a.id === agentId)
  return info?.type === SAVER && (info.spawnedBy === undefined || info.spawnedBy === $.plugin.name)
}

export function consolidatePath($: Api): string {
  return `${$.plugin.root}/scripts/consolidate-project.py`
}

export function detectPrompt(doc: ProjectDoc, claudeMd: string, saved: string[]): string {
  return [
    'What findings, decisions or progress from this conversation are not yet in the project files below?',
    'Answer exactly `NOTHING`, or one line per item: `- <target file>: <item>`.',
    '',
    `Project: ${doc.frontmatter.project ?? 'unknown'} (progress ${doc.progress.done}/${doc.progress.total})`,
    '',
    'Project CLAUDE.md:',
    claudeMd,
    '',
    'Already saved this session:',
    ...(saved.length ? saved.map(i => `- ${i}`) : ['(none)']),
  ].join('\n')
}

export function parseDetected(text: string): string[] | null {
  if (text.trim().toUpperCase() === 'NOTHING') return null
  const items = text
    .split('\n')
    .map(l => l.trim())
    .filter(l => l.startsWith('-'))
    .map(l => l.replace(/^-\s*/, ''))
    .filter(Boolean)
  return items.length ? items : null
}

export function applyPrompt(a: Attached, items: string[], consolidate: string, now?: string): string {
  return [
    `Project directory: ${a.dir}`,
    'Save these items:',
    ...items.map(i => `- ${i}`),
    '',
    `Edit only \`*.md\` files under \`${a.dir}\`. Never change \`status:\`. Update \`last-active:\` to now${now ? ` (${now})` : ''}.`,
    `If CLAUDE.md would exceed 100 lines, run exactly \`python3 "${consolidate}" ${a.name}\` first.`,
    'End with one line: `updated: <summary>`, `nothing`, or `failed: <error>`.',
  ].join('\n')
}

function resolve(path: string): string {
  const out: string[] = []
  for (const seg of path.split('/')) {
    if (seg === '' || seg === '.') continue
    if (seg === '..') out.pop()
    else out.push(seg)
  }
  return `/${out.join('/')}`
}

export function isAllowedForSaver(a: Attached, tool: string, input: unknown, consolidate: string): boolean {
  if (typeof input !== 'object' || input === null) return false
  if (tool === 'Edit' || tool === 'Write') {
    const p = (input as { file_path?: unknown }).file_path
    if (typeof p !== 'string' || !p.startsWith('/')) return false
    const full = resolve(p)
    return full.startsWith(`${resolve(a.dir)}/`) && full.endsWith('.md')
  }
  if (tool === 'Bash') return (input as { command?: unknown }).command === `python3 "${consolidate}" ${a.name}`
  return false
}

function localStamp(ms: number): string {
  const d = new Date(ms)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

type SkipWhy = 'detached' | 'no-new-turns' | 'disabled' | 'in-flight'

// Gates in the order the brief fixes; pure so every automatic trigger is testable.
export function gate(
  trigger: Trigger,
  autoSave: boolean,
  s: { isAttached: boolean; inFlight: boolean; turnsSinceSave: number },
): SkipWhy | null {
  const manual = trigger === 'manual'
  if (!s.isAttached) return 'detached'
  if (!manual && !autoSave) return 'disabled'
  if (s.inFlight) return 'in-flight'
  if (!manual && s.turnsSinceSave === 0) return 'no-new-turns'
  return null
}

// A symlink (or a path that resolves outside the project) is never auto-allowed.
// `stat` rejects ENOENT for a missing path and for a dangling link alike, so a
// missing path counts as new only when its folder is a real folder inside the
// project and lists no entry by that name. Fails closed: the caller then asks.
export async function isPlainTarget($: Api, a: Attached, path: string): Promise<boolean> {
  try {
    const root = (await $.fs.stat(a.dir, { resolve: true })).realPath ?? a.dir
    const isInside = (real: string | undefined) => real !== undefined && (real === root || real.startsWith(`${root}/`))
    try {
      const st = await $.fs.stat(path, { resolve: true })
      return !st.isLink && isInside(st.realPath)
    } catch (e) {
      if (!String(e).includes('ENOENT')) return false
    }
    const cut = path.lastIndexOf('/')
    const folder = path.slice(0, cut) || '/'
    const dir = await $.fs.stat(folder, { resolve: true })
    if (dir.isLink || !isInside(dir.realPath)) return false
    const entries = await $.fs.list(folder)
    return !entries.some((entry) => entry.name === path.slice(cut + 1))
  } catch {
    return false
  }
}

export async function startSave($: Api, trigger: Trigger, opts: { autoSave: boolean }): Promise<SaveOutcome> {
  const a = await getAttached($)
  const turns = await getTurnsSinceSave($)
  // Check and claim with no await between, so concurrent triggers cannot both pass.
  const why = gate(trigger, opts.autoSave, { isAttached: a !== null, inFlight, turnsSinceSave: turns })
  if (why !== null || !a) return { kind: 'skipped', why: why ?? 'detached' }
  inFlight = true
  // If no saver ever reports (or the fork hangs), the slot frees itself.
  staleTimer = $.clock.after(STALE_MS, release)
  let dispatched = false
  try {
    const claudeMd = String(await $.fs.read(`${a.dir}/CLAUDE.md`))
    const fork = await $.model.fork({ prompt: detectPrompt(parseProjectDoc(claudeMd), claudeMd, alreadySaved) })
    if ('usage' in fork && fork.usage) {
      $.ui.log(
        `workspace: detect trigger=${trigger} cache_read=${fork.usage.cache_read_input_tokens} input=${fork.usage.input_tokens}`,
        { to: 'debug' },
      )
    }
    if (!fork.isAnswered) return { kind: 'failed', error: fork.reason }
    const items = parseDetected(fork.text)
    if (!items) return { kind: 'nothing' }
    // A detach while detecting has reset the slot; spawn nothing then.
    if ((await getAttached($))?.dir !== a.dir) return { kind: 'skipped', why: 'detached' }
    const now = localStamp(await $.clock.now())
    const spawn = await $.agent.spawn({
      prompt: applyPrompt(a, items, consolidatePath($), now),
      description: `Save notes to ${a.name}`,
      subagentType: SAVER,
    })
    if (spawn.deny !== undefined) return { kind: 'failed', error: spawn.deny }
    await resetTurns($)
    alreadySaved.push(...items)
    dispatched = true
    return { kind: 'dispatched', items, agentId: spawn.agentId }
  } catch (err) {
    return { kind: 'failed', error: err instanceof Error ? err.message : String(err) }
  } finally {
    if (!dispatched) release()
  }
}

function lastLine(answer: string): string {
  return answer.split('\n').map(l => l.trim()).filter(Boolean).at(-1) ?? ''
}

export async function finishSave($: Api, answer: string): Promise<void> {
  // A completion with nothing in flight is a stale saver; it must not free a newer save.
  if (!inFlight) return
  release()
  const last = lastLine(answer)
  const updated = last.match(/^updated:\s*(.*)$/i)
  if (updated) {
    const summary = (updated[1] ?? '').trim()
    const before = new Set((await getFiles($)).map(f => f.path))
    await setSavedAt($, await $.clock.now())
    await refreshFiles($).catch(() => {})
    const touched: Record<string, 'new' | 'updated'> = {}
    for (const f of await getFiles($)) {
      const base = f.path.split('/').pop() ?? f.path
      if (summary.includes(f.path) || summary.includes(base)) touched[f.path] = before.has(f.path) ? 'updated' : 'new'
    }
    await addMarks($, touched)
    await refreshStatus($).catch(() => {})
    $.ui.toast(`workspace: saved — ${summary}`)
  } else if (/^nothing$/i.test(last)) {
    return
  } else {
    const failed = last.match(/^failed:\s*(.*)$/i)
    $.ui.toast(`workspace: save failed — ${failed ? (failed[1] ?? '').trim() : 'no result reported'}`)
  }
}
