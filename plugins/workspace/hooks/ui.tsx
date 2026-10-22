import { onDetach } from './attach'
import { parseProjectDoc, setFrontmatterField } from './project'
import { getAttached, getSavedAt, getTask, setTask as storeTask } from './state'
import type { Api } from './state'

const TASK_CAP = 120
const STATUS_TASK_CAP = 40
const REFRESH_MS = 60_000

type StatusInput = {
  name: string
  done: number
  total: number
  savedAt: number | null
  lastActive?: string
  task: string
  now: number
}

function parseLocal(stamp: string): number | null {
  const m = stamp.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/)
  if (!m) return null
  const [y, mo, d, h, mi] = m.slice(1).map(Number)
  return new Date(y, mo - 1, d, h, mi).getTime()
}

function age(ms: number): string {
  const minutes = Math.floor(Math.max(0, ms) / 60_000)
  if (minutes < 1) return 'now'
  if (minutes < 60) return `${minutes}m`
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h`
  return `${Math.floor(minutes / 1440)}d`
}

export function statusText(v: StatusInput): string {
  const at = v.savedAt ?? (v.lastActive ? parseLocal(v.lastActive) : null)
  const parts = [`⌂ ${v.name} · ${v.done}/${v.total}`]
  if (at !== null) parts.push(`saved ${age(v.now - at)}`)
  if (v.task) {
    parts.push(v.task.length > STATUS_TASK_CAP ? `${v.task.slice(0, STATUS_TASK_CAP - 1)}…` : v.task)
  }
  return parts.join(' · ')
}

export async function refreshStatus($: Api): Promise<void> {
  const a = await getAttached($)
  if (!a) {
    $.ui.status(undefined)
    return
  }
  const doc = parseProjectDoc(String(await $.fs.read(`${a.dir}/CLAUDE.md`)))
  $.ui.status(
    statusText({
      name: a.name,
      ...doc.progress,
      savedAt: await getSavedAt($),
      lastActive: doc.frontmatter['last-active'],
      task: await getTask($),
      now: await $.clock.now(),
    }),
  )
}

let timer: { cancel: () => void } | null = null

export function startStatusTimer($: Api): void {
  timer?.cancel()
  timer = $.clock.every(REFRESH_MS, () => {
    void refreshStatus($).catch(() => {})
  })
  onDetach.push(() => {
    timer?.cancel()
    timer = null
  })
}

export async function setTask($: Api, task: string): Promise<{ ok: boolean; reason?: string }> {
  const a = await getAttached($)
  if (!a) return { ok: false, reason: 'No workspace project is attached.' }
  const clean = task.replace(/\s+/g, ' ').trim().slice(0, TASK_CAP).trim()
  const path = `${a.dir}/CLAUDE.md`
  await storeTask($, clean)
  await $.fs.write(path, setFrontmatterField(String(await $.fs.read(path)), 'current-task', clean))
  await refreshStatus($)
  return { ok: true }
}
