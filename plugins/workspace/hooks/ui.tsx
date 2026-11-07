// The awareness UI: a status-line digest and a pane listing every repo's live
// state and the worktree groups in flight. The pane's refresh button does its
// own rescan inline (see the note in commands.ts on why `$` is never passed
// through a helper function) using the render dispatch's own `$`, exactly as
// the plugin-authoring examples' Button handlers do.

import { atom, read, update } from 'claude-code'
import type { On } from 'claude-code'

import { discoverWorkspace, linkedWorktrees } from './discovery'
import type { Deps } from './discovery'
import type {
  Changes,
  ClosingEntry,
  ConsolidationEntry,
  DocStatus,
  NewProjectEntry,
  PaneTab,
  PrDetails,
  Pr,
  PrDetailsEntry,
  PrsEntry,
  RepoInfo,
  TasksEntry,
  WorkspaceModel,
  WorktreeDetails,
  WorktreeDetailsEntry,
  WorktreeEntry,
  WorktreeRef,
} from './model'
import { planDocSyncFor } from './doc'
import { fetchPrDetails, fetchPrs, isPrsStale, prDetailsKey, prForBranch } from './prs'
import { fetchWorktreeDetails } from './worktree-details'
import { loadProjects } from './projects'
import {
  EMPTY_NEW_PROJECT,
  canCreateProject,
  closeApplyArgs,
  closeToast,
  launchToast,
  needsAttention,
  newProjectPayload,
  parseCloseApply,
  parseCloseCheck,
  parseConsolidation,
  parseNewProject,
  parseTaskEdit,
  parseTasks,
  skillInvocation,
  suggestBranch,
  taskEditArgs,
  toggleRepo,
} from './project-actions'
import type { SkillName, TaskEdit, WorktreeMode } from './project-actions'
import { renderActionsTab, renderProjectsTab } from './projects-view'
import type { ProjectsActions } from './projects-view'

export const PANE_ID = 'workspace'

/**
 * The pane width, in cells, from which the worktree box sits beside the worktrees list instead of below it. Each
 * box is half the width, so this leaves about 46 cells of content in each once borders and padding are out.
 */
const SIDE_BY_SIDE_MIN_COLUMNS = 100

/** The pane's tabs, in the order they are drawn. */
export const PANE_TABS: readonly PaneTab[] = ['repos', 'projects', 'actions', 'settings']

// Declared locally per the state-scan rule (see model.ts's header comment);
// the `{ plugin, key }` pair is what makes this the same slot register.tsx
// and commands.ts read and write. `activeTab`, `selectedRepo`, `pickerOpen`, `openWorktree`,
// `worktreeDetails` and `prDetails` are only this file's; `prs` is shared with register.tsx, which loads it in the background at session start.
const workspaceModel = atom({ plugin: 'workspace', key: 'model' } as const, null as WorkspaceModel | null)
const docStatus = atom({ plugin: 'workspace', key: 'docStatus' } as const, null as DocStatus)
const activeTab = atom({ plugin: 'workspace', key: 'activeTab' } as const, 'repos' as PaneTab)
const selectedRepo = atom({ plugin: 'workspace', key: 'selectedRepo' } as const, null as string | null)
const prs = atom({ plugin: 'workspace', key: 'prs' } as const, {} as Record<string, PrsEntry>)
const pickerOpen = atom({ plugin: 'workspace', key: 'pickerOpen' } as const, false)
const openWorktree = atom({ plugin: 'workspace', key: 'openWorktree' } as const, null as WorktreeRef | null)
const worktreeDetails = atom(
  { plugin: 'workspace', key: 'worktreeDetails' } as const,
  {} as Record<string, WorktreeDetailsEntry>,
)
const prDetails = atom({ plugin: 'workspace', key: 'prDetails' } as const, {} as Record<string, PrDetailsEntry>)
const openProject = atom({ plugin: 'workspace', key: 'openProject' } as const, null as string | null)
const showDoneTasks = atom({ plugin: 'workspace', key: 'showDoneTasks' } as const, false)
const projectTasks = atom({ plugin: 'workspace', key: 'projectTasks' } as const, {} as Record<string, TasksEntry>)
const consolidation = atom({ plugin: 'workspace', key: 'consolidation' } as const, null as ConsolidationEntry | null)
const closing = atom({ plugin: 'workspace', key: 'closing' } as const, null as ClosingEntry | null)
const newProject = atom({ plugin: 'workspace', key: 'newProject' } as const, null as NewProjectEntry | null)

/** A tab's label, with the number of repos on the Repos tab and of open projects on the Projects tab. */
export function tabLabel(tab: PaneTab, model: WorkspaceModel): string {
  if (tab === 'settings') return 'Settings'
  if (tab === 'actions') return 'Actions'
  if (tab === 'projects') {
    const active = model.projects.filter(p => !p.finished).length
    return model.managed && active > 0 ? `Projects (${active})` : 'Projects'
  }
  return model.mode === 'single-repo' ? 'Repo' : `Repos (${model.repos.length})`
}

/** The status line toggle's label: the action pressing it takes, given the current state. */
export function statusLineToggleLabel(isOn: boolean): string {
  return isOn ? 'Disable' : 'Enable'
}

/** Why there is no doc to sync: the folder is one repo, so there is no workspace map to write. */
export const SINGLE_REPO_DOC_NOTE = 'This folder is a single repo, so there is no workspace map to write.'

/** The toast after a rescan, or after a workspace-doc sync pressed in the Settings tab. */
export function refreshToast(
  model: WorkspaceModel,
  docFile: string,
  outcome: { isDocSync: boolean; wroteDoc: boolean },
): string {
  if (model.mode === 'single-repo') {
    return outcome.isDocSync ? SINGLE_REPO_DOC_NOTE : 'Rescanned the repo and its worktrees.'
  }
  if (outcome.isDocSync) {
    if (model.repos.length === 0) return 'No repos discovered; nothing to write.'
    return outcome.wroteDoc
      ? `Updated the workspace map in ${docFile}.`
      : `${docFile} workspace map is already up to date.`
  }
  const scanned = `Rescanned: ${model.repos.length} git repos, ${model.otherFolders.length} non-git folders.`
  return outcome.wroteDoc ? `${scanned} Updated ${docFile}.` : scanned
}

/** `undefined` clears the status line (nothing discovered yet). */
export function statusLineText(model: WorkspaceModel | null): string | undefined {
  if (model === null || model.repos.length === 0) return undefined
  if (model.mode === 'single-repo') return singleRepoStatusText(model.repos[0]!, model.worktreeGroups.length)
  const dirty = model.repos.filter(r => !r.isClean).length
  const behindOrAhead = model.repos.filter(r => r.ahead > 0 || r.behind > 0)
  const groups = model.worktreeGroups.filter(g => g.members.length > 1)

  const parts = [`${model.repos.length} repos`]
  if (dirty > 0) parts.push(`${dirty} dirty`)
  for (const r of behindOrAhead.slice(0, 2)) {
    const arrows = [r.ahead > 0 ? `↑${r.ahead}` : null, r.behind > 0 ? `↓${r.behind}` : null].filter(Boolean).join('')
    parts.push(`${r.name} ${arrows}`)
  }
  if (groups.length > 0) parts.push(`${groups.length} feature${groups.length > 1 ? 's' : ''} across repos`)

  return parts.join(' · ')
}

/** One repo's digest: its name and branch, whether it is clean, how far it is from upstream, its worktrees. */
function singleRepoStatusText(r: RepoInfo, worktrees: number): string {
  const parts = [r.name, r.branch ?? '(detached)']
  if (!r.isClean) parts.push(`${r.dirtyCount} dirty`)
  const arrows = [r.ahead > 0 ? `↑${r.ahead}` : null, r.behind > 0 ? `↓${r.behind}` : null].filter(Boolean).join('')
  if (arrows) parts.push(arrows)
  if (worktrees > 0) parts.push(`${worktrees} worktree${worktrees > 1 ? 's' : ''}`)
  return parts.join(' · ')
}

/** What to pin as the status line: the digest when the setting is on, `undefined` (clear it) when off. */
export function statusLineFor(model: WorkspaceModel | null, isEnabled: boolean): string | undefined {
  return isEnabled ? statusLineText(model) : undefined
}

/**
 * A worktree's list item: its branch, or marked detached. Its path goes on the next line. With `isCurrent`
 * it is marked like the repo picker marks its options, a filled dot for the worktree the session is in and
 * an open one for the rest; without it, a plain bullet.
 */
export function renderWorktreeName(branch: string | null, isCurrent?: boolean): string {
  const marker = isCurrent === undefined ? '•' : isCurrent ? '●' : '○'
  return `${marker} ${branch ?? '(detached)'}`
}

export type StatusSegment = {
  text: string
  tone: 'pr' | 'open' | 'draft' | 'changed' | 'new' | 'deleted' | 'separator'
}

/**
 * What changed in a worktree as pieces to colour: "3 changed", "2 new", "1 deleted", with a separator between
 * the ones present. Empty for a clean worktree or one that couldn't be checked.
 */
export function changeSegments(changes: Changes | undefined): StatusSegment[] {
  if (changes === undefined) return []
  const parts: StatusSegment[] = []
  if (changes.changed > 0) parts.push({ text: `${changes.changed} changed`, tone: 'changed' })
  if (changes.added > 0) parts.push({ text: `${changes.added} new`, tone: 'new' })
  if (changes.deleted > 0) parts.push({ text: `${changes.deleted} deleted`, tone: 'deleted' })
  return parts.flatMap((part, i) => (i === 0 ? [part] : [{ text: '·', tone: 'separator' as const }, part]))
}

/**
 * A worktree row's status line as pieces to colour: its PR ("#3029", then "Open" or "Draft") when the branch has
 * one, then what changed, a separator between the two. Only open PRs are listed, so the status is open or draft.
 * Empty when there is neither.
 */
export function statusSegments(pr: Pick<Pr, 'number' | 'isDraft'> | undefined, changes: Changes | undefined): StatusSegment[] {
  const changed = changeSegments(changes)
  if (pr === undefined) return changed
  const prPart: StatusSegment[] = [
    { text: `#${pr.number}`, tone: 'pr' },
    pr.isDraft ? { text: 'Draft', tone: 'draft' } : { text: 'Open', tone: 'open' },
  ]
  return changed.length === 0 ? prPart : [...prPart, { text: '·', tone: 'separator' }, ...changed]
}

/** The theme colour for each kind of piece; a separator is drawn dim, and a PR number plain. */
const STATUS_COLORS = { open: 'success', draft: 'inactive', changed: 'warning', new: 'success', deleted: 'error' } as const

/** The repo picker's toggle: the repo shown, with an arrow that says whether its list is open. */
export function repoToggleText(repoName: string, isOpen: boolean): string {
  return `${repoName} ${isOpen ? '▴' : '▾'}`
}

/** A repo's entry in the picker's list: a filled dot for the repo shown, an open one for the rest. */
export function repoOptionLabel(repoName: string, isCurrent: boolean): string {
  return `${isCurrent ? '●' : '○'} ${repoName}`
}

/** The upstream a worktree tracks and how far ahead or behind it is; says so when it is level or tracks nothing. */
export function trackingText(d: Pick<WorktreeDetails, 'upstream' | 'ahead' | 'behind'>): string {
  if (d.upstream === null) return 'no upstream'
  const arrows = [d.ahead > 0 ? `↑${d.ahead}` : null, d.behind > 0 ? `↓${d.behind}` : null].filter(Boolean).join(' ')
  return `${d.upstream} · ${arrows || 'up to date'}`
}

/** A worktree's latest commit as the line above its message: short hash, author and how long ago. */
export function lastCommitText(commit: WorktreeDetails['lastCommit'], now: number): string {
  if (commit === null) return 'none yet'
  return `${commit.hash} · ${commit.author}, ${formatAge(commit.date, now)}`
}

const MAX_MESSAGE_LINES = 12

/**
 * A commit message for the box: trimmed, and cut to twelve lines with an ellipsis. Details saved before
 * messages were kept have none (`undefined`), and state outlives a reload, so that reads as "(no message)"
 * rather than throwing.
 */
export function commitMessageText(message: string | undefined): string {
  const text = message?.trim() ?? ''
  if (text === '') return '(no message)'
  const lines = text.split('\n')
  return lines.length > MAX_MESSAGE_LINES ? [...lines.slice(0, MAX_MESSAGE_LINES), '…'].join('\n') : text
}

/** Why there are no PR buttons yet, or why the lookup failed; nothing once the PRs are in. */
export function prsNote(entry: PrsEntry | undefined): string | undefined {
  if (entry === undefined) return 'PRs not loaded yet.'
  if (entry.status === 'loading') return 'Loading PRs…'
  return entry.status === 'error' ? entry.message : undefined
}

/** How long ago an ISO time was: "just now", "5m ago", "3h ago" or "2d ago". */
export function formatAge(iso: string, now: number): string {
  const then = Date.parse(iso)
  if (Number.isNaN(then)) return 'unknown'
  const minutes = Math.floor(Math.max(0, now - then) / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  return hours < 24 ? `${hours}h ago` : `${Math.floor(hours / 24)}d ago`
}

/**
 * The number of unresolved review threads; a plus when it is only a lower bound, "unknown" without one.
 * Details saved by an older version, before the count existed, have none (`undefined`), and state outlives
 * a reload, so that has to read as unknown rather than throw.
 */
export function unresolvedLabel(unresolved: PrDetails['unresolved'] | undefined): string {
  if (unresolved === null || unresolved === undefined) return 'unknown'
  if (unresolved.count === 0 && !unresolved.isLowerBound) return 'none'
  return `${unresolved.count}${unresolved.isLowerBound ? '+' : ''}`
}

/** A PR's checks as "30 passed · 1 pending", leaving out the counts that are zero. */
export function checksLabel(checks: PrDetails['checks']): string {
  const parts = [
    checks.passed > 0 ? `${checks.passed} passed` : null,
    checks.failed > 0 ? `${checks.failed} failed` : null,
    checks.pending > 0 ? `${checks.pending} pending` : null,
  ].filter(Boolean)
  return parts.length > 0 ? parts.join(' · ') : 'none'
}

/** A PR's status for the box header: its state, with an open draft called a draft. */
export function prStatusLabel(d: Pick<PrDetails, 'state' | 'isDraft'>): string {
  if (d.state === 'OPEN' && d.isDraft) return 'Draft'
  return d.state.charAt(0).toUpperCase() + d.state.slice(1).toLowerCase()
}

/** The theme color for a PR's status, so the badge follows the person's theme. */
export function prStatusColor(d: Pick<PrDetails, 'state' | 'isDraft'>): string {
  if (d.state === 'OPEN') return d.isDraft ? 'inactive' : 'success'
  if (d.state === 'MERGED') return 'merged'
  return d.state === 'CLOSED' ? 'error' : 'text'
}

/** Backslash-escapes what markdown would read as formatting, so a PR title shows as written, on one line. */
function escapeMarkdown(text: string): string {
  return text.replace(/\s+/g, ' ').replace(/[\\`*_[\]<>]/g, '\\$&')
}

/**
 * The PR box's body as markdown: the title a header, the branches and author under it, unresolved
 * review comments, checks and size as a list, and the PR as a link the surface opens. The status is in
 * the box's header, not here.
 */
export function renderPrDetailsMarkdown(d: PrDetails, now: number): string {
  const code = (text: string) => `\`${text.replace(/`/g, "'")}\``
  const url = d.url.replace(/\)/g, '%29').replace(/\s/g, '%20')
  return [
    `## ${escapeMarkdown(d.title)}`,
    '',
    `${code(d.branch)} → ${code(d.baseBranch)} · by ${d.author} · updated ${formatAge(d.updatedAt, now)}`,
    '',
    `- **Unresolved review comments:** ${unresolvedLabel(d.unresolved)}`,
    `- **Checks:** ${checksLabel(d.checks)}`,
    `- **Changes:** +${d.additions} −${d.deletions} · ${d.changedFiles} ${d.changedFiles === 1 ? 'file' : 'files'}`,
    '',
    `[Open on GitHub](${url})`,
  ].join('\n')
}

/** The repo the Repos tab shows: the one picked, else the first. */
export function resolveSelectedRepo<R extends { name: string }>(repos: readonly R[], name: string | null): R | null {
  return repos.find(r => r.name === name) ?? repos[0] ?? null
}

/** The workspace's name for the pane's header: its root folder's name. */
export function workspaceName(root: string): string {
  const trimmed = root.replace(/\/+$/, '')
  return trimmed.slice(trimmed.lastIndexOf('/') + 1) || root
}

export type UiConfig = {
  workspaceRoot?: string
  statusLine: boolean
  autoSyncDoc: boolean
  docFile: 'AGENTS.md' | 'CLAUDE.md'
}

export function registerUi(on: On, config: UiConfig) {
  on('ui.render', { component: 'Pane', requestId: PANE_ID }, async ($, e) => {
    const els = $.ui.resolve(e)
    const { Box, Text, Button, Markdown } = els
    const model = await read($, workspaceModel)
    const status = await read($, docStatus)
    const active = await read($, activeTab)
    // The pane is told its width on every draw, and drawn again when it changes.
    const isNarrow = e.props.bodyColumns < SIDE_BY_SIDE_MIN_COLUMNS
    const picked = await read($, selectedRepo)
    const pickerIsOpen = await read($, pickerOpen)
    const prsByRepo = await read($, prs)
    const openWt = await read($, openWorktree)
    const worktreeDetailsByPath = await read($, worktreeDetails)
    const detailsByKey = await read($, prDetails)
    const openProjectName = await read($, openProject)
    const showDone = await read($, showDoneTasks)
    const tasksByProject = await read($, projectTasks)
    const consolidationEntry = await read($, consolidation)
    const closingEntry = await read($, closing)
    const formEntry = await read($, newProject)
    // `$.state` outlives a hot reload, so a tab removed since (a stored 'folders') falls back to Repos.
    const shown: PaneTab = PANE_TABS.includes(active) ? active : 'repos'

    // Rescans the workspace; with `isDocSync` it also writes the managed block into the root doc
    // whether or not auto-sync is on. Every button calls it through an explicit wrapper, never as
    // `onPress={refresh}`, so an event the engine passes to `onPress` can't be read as `isDocSync`.
    const refresh = async (isDocSync: boolean) => {
      const root = config.workspaceRoot || (await $.session.root())
      const deps: Deps = {
        exec: (argv, cwd, env) => $.process.run(argv, { cwd, env }),
        readFile: path => $.fs.read(path).then(t => (typeof t === 'string' ? t : null)).catch(() => null),
        listDir: path =>
          $.fs
            .list(path)
            .then(entries => entries.map(entry => ({ name: entry.name, isDir: entry.kind === 'dir' })))
            .catch(() => []),
        join: (...parts) => parts.join('/').replace(/\/+/g, '/'),
        now: () => Date.now(),
      }
      const fresh = await discoverWorkspace(root, deps, { projectsScript: `${$.plugin.root}/scripts/projects.py` })
      await update($, workspaceModel, () => fresh)
      $.ui.status(statusLineFor(fresh, config.statusLine))
      const docPath = `${root}/${config.docFile}`
      const existing = await deps.readFile(docPath)
      const plan = planDocSyncFor(fresh, existing, isDocSync || config.autoSyncDoc)
      await update($, docStatus, () => plan.status)
      if (plan.write !== null) await $.fs.write(docPath, plan.write)
      $.ui.toast(refreshToast(fresh, config.docFile, { isDocSync, wroteDoc: plan.write !== null }))
    }

    if (model === null) {
      return (
        <Box flexDirection="column">
          <Text dimColor>No workspace scanned yet.</Text>
          <Button key="refresh" label="Refresh" onPress={() => refresh(false)} />
        </Box>
      )
    }

    // Writes the `statusLine` option through the config system, which reloads the mod with the new
    // value; the line is also set here so the change shows at once rather than after the reload.
    const toggleStatusLine = async () => {
      const next = !config.statusLine
      const result = await $.config.set({ key: `${$.plugin.name}.statusLine`, value: next })
      if (result.deny !== undefined) {
        $.ui.toast(`Couldn't change the status line setting: ${result.deny}`)
        return
      }
      $.ui.status(statusLineFor(model, next))
      $.ui.toast(`Status line ${next ? 'on' : 'off'}.`)
    }

    // ---- Projects and Actions tabs -------------------------------------------------------------------
    // Every file a project owns is edited by a script shared with the skills; these closures only run it,
    // read what it printed and move the pane's own state. Each `$` call stays at its own call site.

    const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err))

    // Runs one of the plugin's scripts against the workspace root, which it is told in WORKSPACE_ROOT.
    const runScript = async (script: string, args: string[], stdin?: string) => {
      const root = config.workspaceRoot || (await $.session.root())
      return $.process.run(['python3', `${$.plugin.root}/scripts/${script}`, ...args], {
        cwd: root,
        env: { WORKSPACE_ROOT: root },
        timeoutMs: 120000,
        ...(stdin === undefined ? {} : { stdin }),
      })
    }

    // Re-reads the projects only (task counts, a status that just changed), not every repo.
    const reloadProjects = async () => {
      const root = config.workspaceRoot || (await $.session.root())
      const fresh = await loadProjects(
        (argv, cwd, env) => $.process.run(argv, { cwd, env }),
        `${$.plugin.root}/scripts/projects.py`,
        root,
      )
      await update($, workspaceModel, cur => (cur === null ? cur : { ...cur, projects: fresh.projects, projectsError: fresh.error }))
    }

    // A full rescan without the toast, for after something that changed worktrees or created a project.
    const rescan = async () => {
      const root = config.workspaceRoot || (await $.session.root())
      const deps: Deps = {
        exec: (argv, cwd, env) => $.process.run(argv, { cwd, env }),
        readFile: path => $.fs.read(path).then(t => (typeof t === 'string' ? t : null)).catch(() => null),
        listDir: path =>
          $.fs
            .list(path)
            .then(entries => entries.map(entry => ({ name: entry.name, isDir: entry.kind === 'dir' })))
            .catch(() => []),
        join: (...parts) => parts.join('/').replace(/\/+/g, '/'),
        now: () => Date.now(),
      }
      const rescanned = await discoverWorkspace(root, deps, { projectsScript: `${$.plugin.root}/scripts/projects.py` })
      await update($, workspaceModel, () => rescanned)
      $.ui.status(statusLineFor(rescanned, config.statusLine))
    }

    // Starts a skill as if it had been typed. It waits for Claude to be idle, so it is not awaited.
    const launch = (skill: SkillName, args = '') => {
      const invocation = skillInvocation(skill, args)
      $.ui.toast(launchToast(invocation))
      // `$.command.run` is asked first; when it refuses the name, the same line goes in as the user's own prompt.
      $.command.run(invocation).catch(async () => {
        try {
          await $.prompt.submit({ text: `/${invocation.command}${invocation.args ? ` ${invocation.args}` : ''}`, asUser: true })
        } catch (err) {
          $.ui.toast(`Couldn't start /${invocation.command}: ${errorText(err)}`)
        }
      })
    }

    // `quiet` keeps the list on screen while it reloads after an edit, instead of flashing "Loading…".
    const loadTasks = async (name: string, quiet: boolean) => {
      if (!quiet) await update($, projectTasks, cur => ({ ...cur, [name]: { status: 'loading' } }))
      let entry: TasksEntry
      try {
        const parsed = parseTasks(await runScript('project-tasks.py', ['list', name]))
        entry = parsed.ok ? { status: 'ok', sections: parsed.sections } : { status: 'error', message: parsed.message }
      } catch (err) {
        entry = { status: 'error', message: `Couldn't read the tasks: ${errorText(err)}` }
      }
      await update($, projectTasks, cur => ({ ...cur, [name]: entry }))
    }

    // Opens a project's box, or closes it when it is the one open. Anything half-done in another project goes.
    const showProject = async (name: string) => {
      if (openProjectName === name) {
        await update($, openProject, () => null)
        return
      }
      await update($, openProject, () => name)
      await update($, consolidation, () => null)
      await update($, closing, () => null)
      await loadTasks(name, false)
    }

    const editTask = async (name: string, edit: TaskEdit) => {
      try {
        const result = parseTaskEdit(await runScript('project-tasks.py', taskEditArgs(name, edit)))
        if (result === 'stale') $.ui.toast('The tasks changed on disk, so they were reloaded.')
        else if (result !== 'ok') $.ui.toast(result.message)
      } catch (err) {
        $.ui.toast(`Couldn't change the task: ${errorText(err)}`)
      }
      await loadTasks(name, true)
      await reloadProjects()
    }

    const startConsolidate = async (name: string) => {
      if (openProjectName !== name) await showProject(name)
      await update($, closing, () => null)
      await update($, consolidation, () => ({ project: name, status: 'loading' }) as ConsolidationEntry)
      let entry: ConsolidationEntry
      try {
        const parsed = parseConsolidation(await runScript('consolidate-project.py', ['--dry-run', name]))
        entry = !parsed.ok
          ? { project: name, status: 'error', message: parsed.message }
          : parsed.kind === 'preview'
            ? { project: name, status: 'preview', lines: parsed.lines, sections: parsed.sections }
            : parsed.kind === 'note'
              ? { project: name, status: 'note', message: parsed.message }
              : { project: name, status: 'error', message: 'Unexpected answer from consolidate-project.py' }
      } catch (err) {
        entry = { project: name, status: 'error', message: `Couldn't check: ${errorText(err)}` }
      }
      await update($, consolidation, () => entry)
    }

    const confirmConsolidate = async (name: string) => {
      await update($, consolidation, () => ({ project: name, status: 'loading' }) as ConsolidationEntry)
      let entry: ConsolidationEntry
      try {
        const parsed = parseConsolidation(await runScript('consolidate-project.py', [name]))
        entry =
          parsed.ok && parsed.kind === 'done'
            ? { project: name, status: 'done', archived: parsed.archived, before: parsed.before, after: parsed.after }
            : { project: name, status: 'error', message: parsed.ok ? 'Nothing was archived.' : parsed.message }
      } catch (err) {
        entry = { project: name, status: 'error', message: `Couldn't archive: ${errorText(err)}` }
      }
      await update($, consolidation, () => entry)
      await loadTasks(name, true)
      await reloadProjects()
    }

    const startClose = async (name: string) => {
      if (openProjectName !== name) await showProject(name)
      await update($, consolidation, () => null)
      await update($, closing, () => ({ project: name, status: 'loading' }) as ClosingEntry)
      let entry: ClosingEntry
      try {
        const parsed = parseCloseCheck(await runScript('close-project.py', ['check', name]))
        entry = parsed.ok
          ? {
              project: name,
              status: 'ready',
              alreadyDone: parsed.alreadyDone,
              worktrees: parsed.worktrees,
              notes: '',
              mode: parsed.worktrees.some(needsAttention) ? 'keep' : 'remove',
            }
          : { project: name, status: 'error', message: parsed.message }
      } catch (err) {
        entry = { project: name, status: 'error', message: `Couldn't check the project: ${errorText(err)}` }
      }
      await update($, closing, () => entry)
    }

    const setCloseNotes = (text: string) =>
      update($, closing, cur => (cur !== null && cur.status === 'ready' ? { ...cur, notes: text } : cur))

    const setCloseMode = (mode: WorktreeMode) =>
      update($, closing, cur => (cur !== null && cur.status === 'ready' ? { ...cur, mode } : cur))

    const confirmClose = async () => {
      if (closingEntry === null || closingEntry.status !== 'ready') return
      const name = closingEntry.project
      try {
        const parsed = parseCloseApply(await runScript('close-project.py', closeApplyArgs(name, closingEntry.notes, closingEntry.mode)))
        if (!parsed.ok) {
          $.ui.toast(parsed.message)
          return
        }
        $.ui.toast(closeToast(parsed))
      } catch (err) {
        $.ui.toast(`Couldn't close the project: ${errorText(err)}`)
        return
      }
      await update($, closing, () => null)
      await rescan()
      await loadTasks(name, true)
    }

    const openForm = async () => {
      await update($, newProject, cur => cur ?? EMPTY_NEW_PROJECT)
      await update($, activeTab, () => 'projects' as PaneTab)
    }

    const setForm = (patch: Partial<NewProjectEntry>) =>
      update($, newProject, cur => {
        if (cur === null) return cur
        const next = { ...cur, ...patch }
        // Until the branch is typed by hand, it follows the description.
        return patch.description !== undefined && !next.isBranchEdited ? { ...next, branch: suggestBranch(next.description) } : next
      })

    const createProject = async () => {
      if (formEntry === null || !canCreateProject(formEntry)) return
      await update($, newProject, cur => (cur === null ? cur : { ...cur, status: 'creating', message: '' }))
      try {
        const parsed = parseNewProject(await runScript('new-project.py', ['create'], newProjectPayload(formEntry)))
        if (!parsed.ok) {
          await update($, newProject, cur => (cur === null ? cur : { ...cur, status: 'error', message: parsed.message }))
          return
        }
        const worktrees = parsed.worktrees > 0 ? ` with ${parsed.worktrees} worktree${parsed.worktrees === 1 ? '' : 's'}` : ''
        const problems = parsed.errors.length > 0 ? ` (${parsed.errors[0]})` : ''
        $.ui.toast(`Created project ${parsed.folder}${worktrees}${problems}.`)
        await update($, newProject, () => null)
        await rescan()
        await update($, openProject, () => parsed.folder)
        await loadTasks(parsed.folder, false)
      } catch (err) {
        await update($, newProject, cur => (cur === null ? cur : { ...cur, status: 'error', message: `Couldn't create the project: ${errorText(err)}` }))
      }
    }

    // A press that fails must not become an unhandled rejection: say so instead.
    const fire = (work: Promise<unknown> | undefined) => {
      work?.catch((err: unknown) => {
        $.ui.toast(`Something went wrong: ${errorText(err)}`)
      })
    }

    const projectActions: ProjectsActions = {
      openProject: name => fire(showProject(name)),
      launch,
      openForm: () => fire(openForm()),
      toggleShowDone: () => fire(update($, showDoneTasks, cur => !cur)),
      toggleTask: (name, section, text, occurrence) => fire(editTask(name, { kind: 'toggle', section, text, occurrence })),
      removeTask: (name, section, text, occurrence) => fire(editTask(name, { kind: 'remove', section, text, occurrence })),
      addTask: (name, section, text) =>
        fire(text.trim() === '' ? undefined : editTask(name, { kind: 'add', section, text: text.trim() })),
      startConsolidate: name => fire(startConsolidate(name)),
      confirmConsolidate: name => fire(confirmConsolidate(name)),
      dismissConsolidate: () => fire(update($, consolidation, () => null)),
      startClose: name => fire(startClose(name)),
      setCloseNotes: text => fire(setCloseNotes(text)),
      setCloseMode: mode => fire(setCloseMode(mode)),
      confirmClose: () => fire(confirmClose()),
      dismissClose: () => fire(update($, closing, () => null)),
      setForm: patch => fire(setForm(patch)),
      toggleFormRepo: name =>
        fire(update($, newProject, cur => (cur === null ? cur : { ...cur, repos: toggleRepo(cur.repos, name) }))),
      createProject: () => fire(createProject()),
      dismissForm: () => fire(update($, newProject, () => null)),
    }

    const projectsTab = renderProjectsTab(
      els,
      {
        model,
        hasFields: e.surface !== 'mobile',
        now: Date.now(),
        openProject: openProjectName,
        showDone,
        tasks: tasksByProject,
        consolidation: consolidationEntry,
        closing: closingEntry,
        form: formEntry,
      },
      projectActions,
    )
    const actionsTab = renderActionsTab(els, projectActions)

    // The pane's own status bar: the workspace name and folder path, with the tab buttons on their own line.
    const header = (
      <Box borderStyle="round" paddingX={1} flexDirection="column">
        <Box>
          <Text bold>{workspaceName(model.root)}</Text>
          <Text dimColor> · {model.root}</Text>
        </Box>
        <Box>
          {PANE_TABS.map(tab => (
            <Button
              key={`tab-${tab}`}
              label={tabLabel(tab, model)}
              variant={tab === shown ? 'primary' : 'secondary'}
              onPress={() => update($, activeTab, () => tab)}
            />
          ))}
        </Box>
      </Box>
    )

    // Fetches a repo's open PRs through `gh`, showing "Loading…" meanwhile. The result, or the reason it
    // failed, lands in state either way.
    const loadPrs = async (repo: RepoInfo) => {
      const loading: PrsEntry = { status: 'loading' }
      await update($, prs, cur => ({ ...cur, [repo.name]: loading }))
      const entry = await fetchPrs((argv, cwd) => $.process.run(argv, { cwd }), repo, Date.now())
      await update($, prs, cur => ({ ...cur, [repo.name]: entry }))
    }

    // The same for one PR's details, which `gh pr view` has and `gh pr list` does not.
    const loadPrDetails = async (repo: RepoInfo, number: number) => {
      const key = prDetailsKey(repo.name, number)
      const loading: PrDetailsEntry = { status: 'loading' }
      await update($, prDetails, cur => ({ ...cur, [key]: loading }))
      const entry = await fetchPrDetails((argv, cwd) => $.process.run(argv, { cwd }), repo, number, Date.now())
      await update($, prDetails, cur => ({ ...cur, [key]: entry }))
    }

    // The same for a worktree's own details: its upstream, last commit and fresh changes.
    const loadWorktreeDetails = async (path: string) => {
      const loading: WorktreeDetailsEntry = { status: 'loading' }
      await update($, worktreeDetails, cur => ({ ...cur, [path]: loading }))
      const entry = await fetchWorktreeDetails((argv, cwd) => $.process.run(argv, { cwd }), path, Date.now())
      await update($, worktreeDetails, cur => ({ ...cur, [path]: entry }))
    }

    // Pressing a worktree's name, or its PR, opens its box at once. What the box shows is fetched when it has
    // never loaded, failed, or aged out: the worktree's own details, and its PR's when the branch has one.
    const openWorktreeBox = async (repo: RepoInfo, path: string, branch: string | null) => {
      await update($, openWorktree, () => ({ repo: repo.name, path }))
      const now = Date.now()
      const loads: Promise<void>[] = []
      if (isPrsStale((await read($, worktreeDetails))[path], now)) loads.push(loadWorktreeDetails(path))

      const listed = (await read($, prs))[repo.name]
      const pr = listed?.status === 'ok' ? prForBranch(listed.prs, branch) : undefined
      if (pr !== undefined && isPrsStale((await read($, prDetails))[prDetailsKey(repo.name, pr.number)], now)) {
        loads.push(loadPrDetails(repo, pr.number))
      }
      await Promise.all(loads)
    }

    // Picking a repo shows it at once and closes the box, which belongs to the repo it was opened from;
    // the repo's PRs are fetched when they have never loaded, failed, or aged out.
    const selectRepo = async (name: string) => {
      await update($, selectedRepo, () => name)
      await update($, pickerOpen, () => false)
      await update($, openWorktree, () => null)
      const repo = model.repos.find(r => r.name === name)
      if (repo && isPrsStale((await read($, prs))[name], Date.now())) await loadPrs(repo)
    }

    const current = resolveSelectedRepo(model.repos, picked)
    const entry = current === null ? undefined : prsByRepo[current.name]
    const prList = entry?.status === 'ok' ? entry.prs : []
    const note = prsNote(entry)

    const isSingleRepo = model.mode === 'single-repo'

    // The main checkout and each linked worktree, as list rows. A row's name opens its box; under it a status
    // line has the branch's PR, when it has one, and what changed.
    const worktreeRows =
      current === null
        ? []
        : [
            {
              key: current.path,
              name: renderWorktreeName(current.branch, isSingleRepo ? true : undefined),
              branch: current.branch,
              status: statusSegments(prForBranch(prList, current.branch), current.changes),
            },
            ...linkedWorktrees(current).map(w => ({
              key: w.path,
              name: renderWorktreeName(w.branch, isSingleRepo ? false : undefined),
              branch: w.branch,
              status: statusSegments(prForBranch(prList, w.branch), w.changes),
            })),
          ]

    // Status pieces, coloured: a dim separator, a plain PR number, and a theme colour for each of the rest.
    const statusTexts = (segments: StatusSegment[]) =>
      segments.map((segment, i) =>
        segment.tone === 'separator' ? (
          <Text key={`status-${i}`} dimColor>
            {segment.text}
          </Text>
        ) : segment.tone === 'pr' ? (
          <Text key={`status-${i}`}>{segment.text}</Text>
        ) : (
          <Text key={`status-${i}`} color={STATUS_COLORS[segment.tone]}>
            {segment.text}
          </Text>
        ),
      )

    // The box is hidden until a worktree is opened, and shows only for the repo on screen. A worktree that has
    // gone since it was opened has no row, so no box.
    const shownRow =
      current !== null && openWt !== null && openWt.repo === current.name
        ? worktreeRows.find(row => row.key === openWt.path)
        : undefined
    const wtEntry = shownRow === undefined ? undefined : worktreeDetailsByPath[shownRow.key]
    const boxPr = shownRow === undefined ? undefined : prForBranch(prList, shownRow.branch)
    const prEntry =
      boxPr === undefined || current === null ? undefined : detailsByKey[prDetailsKey(current.name, boxPr.number)]

    const worktreeBody =
      wtEntry === undefined || wtEntry.status === 'loading' ? (
        <Text dimColor>Loading details…</Text>
      ) : wtEntry.status === 'error' ? (
        <Text color="yellow">{wtEntry.message}</Text>
      ) : (
        <Box flexDirection="column">
          <Box columnGap={1}>
            <Box flexShrink={0}>
              <Text bold>Tracking:</Text>
            </Box>
            <Text>{trackingText(wtEntry.details)}</Text>
          </Box>
          <Box columnGap={1}>
            <Box flexShrink={0}>
              <Text bold>Last commit:</Text>
            </Box>
            <Text>{lastCommitText(wtEntry.details.lastCommit, Date.now())}</Text>
          </Box>
          {wtEntry.details.lastCommit !== null && (
            <Text dimColor>{commitMessageText(wtEntry.details.lastCommit.message)}</Text>
          )}
          <Box columnGap={1}>
            <Box flexShrink={0}>
              <Text bold>Changes:</Text>
            </Box>
            {changeSegments(wtEntry.details.changes).length === 0 ? (
              <Text dimColor>none</Text>
            ) : (
              <Box flexWrap="wrap" columnGap={1}>
                {statusTexts(changeSegments(wtEntry.details.changes))}
              </Box>
            )}
          </Box>
        </Box>
      )

    const prBody =
      prEntry === undefined || prEntry.status === 'loading' ? (
        <Text dimColor>Loading PR details…</Text>
      ) : prEntry.status === 'error' ? (
        <Text color="yellow">{prEntry.message}</Text>
      ) : (
        <Markdown text={renderPrDetailsMarkdown(prEntry.details, Date.now())} />
      )

    const worktreeBox =
      shownRow === undefined ? null : (
        <Box key="worktree-box" borderStyle="single" paddingX={1} flexDirection="column" width={isNarrow ? '100%' : '50%'}>
          <Box key="worktree-header" borderStyle="round" paddingX={1} justifyContent="space-between">
            <Text bold>{shownRow.branch ?? '(detached)'}</Text>
            <Box flexShrink={0}>
              <Button key="close-worktree" label="Close" onPress={() => update($, openWorktree, () => null)} />
            </Box>
          </Box>
          <Text dimColor>{shownRow.key}</Text>
          <Text> </Text>
          {worktreeBody}
          {boxPr !== undefined && (
            <Box flexDirection="column">
              <Box key="pr-header" borderStyle="round" paddingX={1} columnGap={1}>
                <Box flexShrink={0}>
                  <Text bold>{`PR #${boxPr.number}`}</Text>
                </Box>
                {prEntry?.status === 'ok' && (
                  <Text color={prStatusColor(prEntry.details)}>{prStatusLabel(prEntry.details)}</Text>
                )}
              </Box>
              {prBody}
            </Box>
          )}
        </Box>
      )

    // One repo at a time, picked from the list: its worktrees as a list, and beside it the box of the one opened.
    const reposTab =
      current === null ? (
        <Text dimColor>No git repos found under {model.root}.</Text>
      ) : (
        <Box flexDirection="column">
          {/* A dropdown made of buttons, not a Select: a click presses a button, while a Select takes keys only. */}
          {/* The bold label is beside the button, since a button has no bold of its own. */}
          {/* A single repo has nothing to pick between, so the header already names it. */}
          {!isSingleRepo && (
          <Box columnGap={1}>
            <Box flexShrink={0}>
              <Text bold>Repo:</Text>
            </Box>
            <Button
              key="repo-picker-toggle"
              plain
              label={repoToggleText(current.name, pickerIsOpen)}
              onPress={() => update($, pickerOpen, open => !open)}
            />
          </Box>
          )}
          {!isSingleRepo && pickerIsOpen && (
            <Box key="repo-list" borderStyle="round" paddingX={1} flexDirection="column">
              {model.repos.map(r => (
                <Button
                  key={`repo-${r.name}`}
                  plain
                  label={repoOptionLabel(r.name, r.name === current.name)}
                  onPress={() => selectRepo(r.name)}
                />
              ))}
            </Box>
          )}
          {current.error !== undefined && <Text color="error">{`Scan failed: ${current.error}`}</Text>}
          {/* Side by side when there is room, stacked (the worktree box below the list) when there isn't. */}
          <Box key="repos-body" flexDirection={isNarrow ? 'column' : 'row'} columnGap={1}>
            <Box
              key="worktrees-box"
              borderStyle="single"
              paddingX={1}
              flexDirection="column"
              width={worktreeBox === null || isNarrow ? '100%' : '50%'}
            >
              <Box key="worktrees-header" justifyContent="space-between">
                <Text bold>{`Worktrees (${worktreeRows.length})`}</Text>
                <Box flexShrink={0}>
                  <Button key="reload-prs" label="Refresh" onPress={() => loadPrs(current)} />
                </Box>
              </Box>
              {note !== undefined &&
                (entry?.status === 'error' ? <Text color="yellow">{note}</Text> : <Text dimColor>{note}</Text>)}
              {worktreeRows.map(row => (
                <Box key={row.key} flexDirection="column">
                  <Button
                    key={`worktree-${row.key}`}
                    plain
                    label={row.name}
                    onPress={() => openWorktreeBox(current, row.key, row.branch)}
                  />
                  {row.status.length > 0 && (
                    <Box paddingLeft={2} columnGap={1} flexWrap="wrap">
                      {statusTexts(row.status)}
                    </Box>
                  )}
                </Box>
              ))}
            </Box>
            {worktreeBox !== null && worktreeBox}
          </Box>
        </Box>
      )

    const settingsTab = (
      <Box flexDirection="column">
        <Text bold>Status line: {config.statusLine ? 'On' : 'Off'}</Text>
        <Text dimColor>A one-line digest of the workspace, pinned under the prompt.</Text>
        <Button
          key="toggle-status-line"
          label={statusLineToggleLabel(config.statusLine)}
          onPress={toggleStatusLine}
        />
        {!isSingleRepo && (
          <Box key="doc-section" flexDirection="column">
            <Text> </Text>
            <Text bold>Workspace doc</Text>
            <Text dimColor>Write the repo map into {config.docFile}, between the managed markers only.</Text>
            <Button key="sync-doc" label="Sync workspace doc" onPress={() => refresh(true)} />
          </Box>
        )}
        <Text> </Text>
        <Text bold>Rescan</Text>
        <Text dimColor>{isSingleRepo ? 'Re-read the repo and its worktrees.' : 'Re-read every repo, worktree and folder.'}</Text>
        <Button key="refresh" label="Refresh" onPress={() => refresh(false)} />
      </Box>
    )

    return (
      <Box flexDirection="column">
        {header}
        <Box borderStyle="round" paddingX={1} flexDirection="column">
          {shown === 'settings' ? settingsTab : shown === 'projects' ? projectsTab : shown === 'actions' ? actionsTab : reposTab}
        </Box>
        {status === 'stale' && (
          <Text color="yellow">Workspace doc is stale — sync it from the Settings tab or run /workspace sync-doc</Text>
        )}
      </Box>
    )
  })
}
