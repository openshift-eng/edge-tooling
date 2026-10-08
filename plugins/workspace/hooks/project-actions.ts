// What the Projects and Actions tabs do, as plain functions: the invocation of a skill, the payloads sent to
// the shared scripts (`scripts/project-tasks.py`, `consolidate-project.py`, `close-project.py`,
// `new-project.py`) and the reading of what they print. The scripts own every file edit; nothing here
// touches a project. The `$` calls live in ui.tsx, at each button's own call site.

import type { ExecResult } from './discovery'
import type {
  ClosingWorktree,
  ConsolidationSection,
  NewProjectEntry,
  NewProjectType,
  TaskSection,
} from './model'

type Out = Pick<ExecResult, 'exitCode' | 'stdout' | 'stderr'>
export type Failure = { ok: false; message: string }

const str = (value: unknown): string => (typeof value === 'string' ? value : '')
const num = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) ? value : 0)
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null

/** The script's stdout as an object, or why it can't be read. */
function readObject(name: string, out: Out): { ok: true; body: Record<string, unknown> } | Failure {
  if (out.exitCode !== 0) {
    return { ok: false, message: `${name} failed: ${out.stderr.trim().split('\n')[0] || `exit ${out.exitCode}`}` }
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(out.stdout)
  } catch {
    return { ok: false, message: `${name} printed something that is not JSON` }
  }
  return isRecord(parsed) ? { ok: true, body: parsed } : { ok: false, message: `${name} printed no object` }
}

/** The script's own message for a status it reported as a problem. */
const problem = (body: Record<string, unknown>, fallback: string): string =>
  str(body.error_message) || str(body.error) || fallback

// ---- skills --------------------------------------------------------------------------------------------

/** The plugin's skills are namespaced by its name: `/workspace:resume-project`. */
export const SKILL_PREFIX = 'workspace'

export type SkillName =
  | 'setup-environment'
  | 'create-domain'
  | 'update-domain'
  | 'new-project'
  | 'resume-project'
  | 'update-project'
  | 'handoff'
  | 'consolidate-project'
  | 'close-project'
  | 'auto-update'

/** What `$.command.run` takes for a skill. `args` is what would be typed after the name. */
export function skillInvocation(skill: SkillName, args = ''): { command: string; args: string } {
  return { command: `${SKILL_PREFIX}:${skill}`, args: args.trim() }
}

/** The line of the toast after a launcher press: a skill waits for Claude to be idle. */
export function launchToast(inv: { command: string; args: string }): string {
  return `Running /${inv.command}${inv.args ? ` ${inv.args}` : ''} once Claude is idle`
}

// ---- project rows --------------------------------------------------------------------------------------

/** `3d ago` for a `last-active` stamp (`YYYY-MM-DDTHH:MM`, local time); empty when it can't be read. */
export function relativeTime(stamp: string, now: number): string {
  const then = Date.parse(stamp)
  if (stamp === '' || Number.isNaN(then)) return ''
  const minutes = Math.max(0, Math.floor((now - then) / 60000))
  if (minutes < 60) return 'just now'
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 14) return `${days}d ago`
  if (days < 60) return `${Math.floor(days / 7)}w ago`
  return `${Math.floor(days / 30)}mo ago`
}

/** The dim line under a project's name: its type, status, when it was last active, how far its tasks are. */
export function projectRowText(
  p: { type: string; status: string; lastActive: string; tasks: { checked: number; total: number } },
  now: number,
): string {
  const ago = relativeTime(p.lastActive, now)
  return [
    p.type || null,
    p.status || null,
    ago ? `active ${ago}` : null,
    p.tasks.total > 0 ? `${p.tasks.checked}/${p.tasks.total} tasks` : null,
  ]
    .filter(Boolean)
    .join(' · ')
}

/** The skills a project row offers: a finished project can only be resumed. */
export function rowSkills(finished: boolean): readonly SkillName[] {
  return finished ? ['resume-project'] : ['resume-project', 'update-project', 'handoff']
}

// ---- task list -----------------------------------------------------------------------------------------

export type TasksResult = { ok: true; sections: TaskSection[] } | Failure

export function parseTasks(out: Out): TasksResult {
  const read = readObject('project-tasks.py', out)
  if (!read.ok) return read
  if (read.body.status === 'not_found') return { ok: false, message: 'This project has no CLAUDE.md to read tasks from.' }
  if (read.body.status !== 'ok' || !Array.isArray(read.body.sections)) {
    return { ok: false, message: problem(read.body, 'project-tasks.py reported a problem') }
  }
  const sections = read.body.sections.flatMap((raw: unknown): TaskSection[] => {
    if (!isRecord(raw) || !Array.isArray(raw.items)) return []
    const items = raw.items.flatMap((item: unknown) =>
      isRecord(item) && str(item.text) !== ''
        ? [{ text: str(item.text), checked: item.checked === true, occurrence: num(item.occurrence) }]
        : [],
    )
    return [{ heading: str(raw.heading), level: num(raw.level) || 2, items }]
  })
  return { ok: true, sections }
}

export type TaskEdit = { kind: 'toggle' | 'remove'; section: string; text: string; occurrence: number } | { kind: 'add'; section: string; text: string }

/** The argv after `project-tasks.py` for one edit. Text and heading go in as argv entries, never a shell line. */
export function taskEditArgs(project: string, edit: TaskEdit): string[] {
  const base = [edit.kind, project, '--section', edit.section, '--text', edit.text]
  return edit.kind === 'add' ? base : [...base, '--occurrence', String(edit.occurrence)]
}

/** `ok`, `stale` (the line changed under us), or an error message. */
export function parseTaskEdit(out: Out): 'ok' | 'stale' | Failure {
  const read = readObject('project-tasks.py', out)
  if (!read.ok) return read
  if (read.body.status === 'ok') return 'ok'
  if (read.body.status === 'stale') return 'stale'
  return { ok: false, message: problem(read.body, 'project-tasks.py reported a problem') }
}

// ---- consolidate ---------------------------------------------------------------------------------------

export type ConsolidationResult =
  | { ok: true; kind: 'preview'; lines: number; sections: ConsolidationSection[] }
  | { ok: true; kind: 'note'; message: string }
  | { ok: true; kind: 'done'; archived: number; before: number; after: number }
  | Failure

export function parseConsolidation(out: Out): ConsolidationResult {
  const read = readObject('consolidate-project.py', out)
  if (!read.ok) return read
  const body = read.body
  switch (body.status) {
    case 'needs_consolidation': {
      const sections = (Array.isArray(body.sections) ? body.sections : []).flatMap((s: unknown): ConsolidationSection[] =>
        isRecord(s)
          ? [{ name: str(s.name), checked: num(s.checked), toArchive: num(s.to_archive), toKeep: num(s.to_keep), unchecked: num(s.unchecked), strikethrough: num(s.strikethrough) }]
          : [],
      )
      return { ok: true, kind: 'preview', lines: num(body.claude_md_lines), sections }
    }
    case 'already_lean':
    case 'over_threshold_no_sections':
      return { ok: true, kind: 'note', message: problem(body, 'Nothing to consolidate.') }
    case 'consolidated': {
      const archived = (Array.isArray(body.sections) ? body.sections : []).reduce(
        (sum: number, s: unknown) => sum + (isRecord(s) ? num(s.archived) : 0),
        0,
      )
      return { ok: true, kind: 'done', archived, before: num(body.claude_md_before), after: num(body.claude_md_after) }
    }
    default:
      return { ok: false, message: problem(body, 'consolidate-project.py reported a problem') }
  }
}

// ---- close ---------------------------------------------------------------------------------------------

export type CloseCheck = { ok: true; alreadyDone: boolean; worktrees: ClosingWorktree[] } | Failure

export function parseCloseCheck(out: Out): CloseCheck {
  const read = readObject('close-project.py', out)
  if (!read.ok) return read
  const body = read.body
  if (body.status === 'not_found') return { ok: false, message: 'Project not found.' }
  if (body.status !== 'ok') return { ok: false, message: problem(body, 'close-project.py reported a problem') }
  const worktrees = (Array.isArray(body.worktrees) ? body.worktrees : []).flatMap((w: unknown): ClosingWorktree[] =>
    isRecord(w) && str(w.path) !== ''
      ? [{ repo: str(w.repo), path: str(w.path), branch: str(w.branch), dirty: w.dirty === true, dirtyFiles: num(w.dirty_files), ahead: num(w.ahead), noUpstream: w.no_upstream === true }]
      : [],
  )
  return { ok: true, alreadyDone: body.already_done === true, worktrees }
}

/** A worktree that holds something a removal would lose. */
export function needsAttention(w: ClosingWorktree): boolean {
  return w.dirty || w.ahead > 0 || w.noUpstream
}

export function worktreeStatusText(w: ClosingWorktree): string {
  const parts = [
    w.dirty ? `dirty (${w.dirtyFiles} file${w.dirtyFiles === 1 ? '' : 's'})` : null,
    w.ahead > 0 ? `ahead by ${w.ahead}` : null,
    w.noUpstream ? 'no upstream' : null,
  ].filter(Boolean)
  return parts.length > 0 ? parts.join(', ') : 'clean'
}

export type WorktreeMode = 'remove' | 'keep' | 'discard'

export type CloseApply = { ok: true; removed: number; kept: number; errors: string[] } | Failure

export function parseCloseApply(out: Out): CloseApply {
  const read = readObject('close-project.py', out)
  if (!read.ok) return read
  if (read.body.status !== 'ok') return { ok: false, message: problem(read.body, 'close-project.py reported a problem') }
  const list = (value: unknown) => (Array.isArray(value) ? value : [])
  return {
    ok: true,
    removed: list(read.body.removed).length,
    kept: list(read.body.kept).length,
    errors: list(read.body.errors).map(String),
  }
}

/** The argv after `close-project.py` to close for real. Empty notes are left out. */
export function closeApplyArgs(project: string, notes: string, mode: WorktreeMode): string[] {
  const trimmed = notes.trim()
  return ['apply', project, ...(trimmed ? ['--notes', trimmed] : []), '--worktrees', mode]
}

export function closeToast(done: { removed: number; kept: number; errors: string[] }): string {
  const bits = ['Project closed']
  if (done.removed > 0) bits.push(`${done.removed} worktree${done.removed === 1 ? '' : 's'} removed`)
  if (done.kept > 0) bits.push(`${done.kept} kept`)
  if (done.errors.length > 0) bits.push(`${done.errors.length} problem${done.errors.length === 1 ? '' : 's'}: ${done.errors[0]}`)
  return bits.join(' · ') + '.'
}

// ---- new project ---------------------------------------------------------------------------------------

export const NEW_PROJECT_TYPES: readonly { value: NewProjectType; label: string }[] = [
  { value: 'bug', label: 'Bug investigation' },
  { value: 'feature', label: 'Feature development' },
  { value: 'ci-testing', label: 'CI / testing' },
  { value: 'docs', label: 'Documentation' },
  { value: 'analysis', label: 'Analysis / review' },
]

export const EMPTY_NEW_PROJECT: NewProjectEntry = {
  description: '',
  type: 'bug',
  jira: '',
  repos: [],
  branch: '',
  isBranchEdited: false,
  status: 'editing',
  message: '',
}

/** A branch name taken from the description: lowercase words joined by dashes, at most 40 characters. */
export function suggestBranch(description: string): string {
  return description
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40)
    .replace(/-+$/, '')
}

export function canCreateProject(form: Pick<NewProjectEntry, 'description' | 'status'>): boolean {
  return form.description.trim() !== '' && form.status !== 'creating'
}

/** The JSON that `new-project.py create` reads on stdin. */
export function newProjectPayload(form: NewProjectEntry): string {
  const branch = form.branch.trim()
  return JSON.stringify({
    description: form.description.trim(),
    type: form.type,
    jira: form.jira.trim() || 'none',
    repos: form.repos,
    // A blank branch means no worktree; without this the script would pick a default branch and make one.
    ...(branch ? { branch } : { no_worktree: true }),
  })
}

export type NewProjectResult = { ok: true; folder: string; worktrees: number; errors: string[] } | Failure

export function parseNewProject(out: Out): NewProjectResult {
  const read = readObject('new-project.py', out)
  if (!read.ok) return read
  const body = read.body
  if (body.status !== 'ok' || str(body.folder) === '') return { ok: false, message: problem(body, 'new-project.py reported a problem') }
  return {
    ok: true,
    folder: str(body.folder),
    worktrees: Array.isArray(body.worktrees) ? body.worktrees.length : 0,
    errors: (Array.isArray(body.errors) ? body.errors : []).map(String),
  }
}

/** Turns a repo on or off in the form's list. */
export function toggleRepo(repos: readonly string[], name: string): string[] {
  return repos.includes(name) ? repos.filter(r => r !== name) : [...repos, name]
}
