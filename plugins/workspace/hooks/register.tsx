// Wires the mod together. Every piece of logic that touches `$` is written
// inline inside the `on(...)` callback that owns it (or a closure defined
// directly inside that callback) — the engine requires `$` to appear only as
// `$.noun.method(...)` at its own call site, or as a direct argument to
// `update`/`read`, never passed through a plugin's own function. Shared logic
// that doesn't need `$` (parsing, matching, rendering text, deciding what to
// write) lives in discovery.ts/doc.ts/guardrails.ts/context.ts/worktrees.ts
// as plain, independently-tested functions.

import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import { discoverWorkspace, scanRepo } from './discovery'
import type { Deps } from './discovery'
import type {
  ClosingEntry,
  ConsolidationEntry,
  DocStatus,
  NewProjectEntry,
  PrDetailsEntry,
  PrsEntry,
  RepoInfo,
  TasksEntry,
  WorkspaceModel,
  WorktreeDetailsEntry,
  WorktreeRef,
} from './model'
import { fetchPrs } from './prs'
import { buildWorkspaceSection, SECTION_ID } from './context'
import { registerUi, PANE_ID, statusLineFor } from './ui'
import { planDocSyncFor } from './doc'
import { evaluateGitCommand, looksLikeGitMutation, resolveRepoForCommand } from './guardrails'
import type { GuardrailPosture } from './guardrails'
import { createWorktree, defaultWorktreeDir } from './worktrees'
import { COMMAND_SPEC, registerWorkspaceCommandHandler } from './commands'

const WORKTREE_TOOL = 'workspace_worktree'
const MCP_WORKTREE_TOOL = 'mcp__workspace__workspace_worktree'
const SAFETY_REFRESH_MS = 10 * 60 * 1000 // low-frequency backstop; most refreshes are event-driven

// Declared locally per the state-scan rule (see model.ts); the `{ plugin, key }`
// pair, not the import, is what makes this the same slot ui.tsx and
// commands.ts read and write.
const workspaceModel = atom({ plugin: 'workspace', key: 'model' } as const, null as WorkspaceModel | null)
const docStatus = atom({ plugin: 'workspace', key: 'docStatus' } as const, null as DocStatus)
const prs = atom({ plugin: 'workspace', key: 'prs' } as const, {} as Record<string, PrsEntry>)
const pickerOpen = atom({ plugin: 'workspace', key: 'pickerOpen' } as const, false)
const openWorktree = atom({ plugin: 'workspace', key: 'openWorktree' } as const, null as WorktreeRef | null)
const worktreeDetails = atom(
  { plugin: 'workspace', key: 'worktreeDetails' } as const,
  {} as Record<string, WorktreeDetailsEntry>,
)
const prDetails = atom({ plugin: 'workspace', key: 'prDetails' } as const, {} as Record<string, PrDetailsEntry>)
const openProject = atom({ plugin: 'workspace', key: 'openProject' } as const, null as string | null)
const projectTasks = atom({ plugin: 'workspace', key: 'projectTasks' } as const, {} as Record<string, TasksEntry>)
const consolidation = atom({ plugin: 'workspace', key: 'consolidation' } as const, null as ConsolidationEntry | null)
const closing = atom({ plugin: 'workspace', key: 'closing' } as const, null as ClosingEntry | null)
const newProject = atom({ plugin: 'workspace', key: 'newProject' } as const, null as NewProjectEntry | null)
const loadingPrs: PrsEntry = { status: 'loading' }

type Options = {
  workspaceRoot?: string
  statusLine?: boolean
  paneAutoOpen?: boolean
  guardrails?: GuardrailPosture
  autoSyncDoc?: boolean
  docFile?: 'AGENTS.md' | 'CLAUDE.md'
}

function joinPath(...parts: string[]): string {
  return parts.join('/').replace(/\/+/g, '/')
}

function replaceRepo(model: WorkspaceModel | null, fresh: RepoInfo): WorkspaceModel | null {
  if (!model) return model
  return { ...model, repos: model.repos.map(r => (r.name === fresh.name ? fresh : r)) }
}

export const register: Register = (on, options) => {
  const opts = options as Options
  const posture: GuardrailPosture = opts.guardrails ?? 'warn'
  const docFile = opts.docFile ?? 'AGENTS.md'
  const autoSyncDoc = opts.autoSyncDoc ?? false
  const statusLine = opts.statusLine ?? true

  registerUi(on, { workspaceRoot: opts.workspaceRoot, statusLine, autoSyncDoc, docFile })
  registerWorkspaceCommandHandler(on, { workspaceRoot: opts.workspaceRoot, statusLine, autoSyncDoc, docFile })

  on('session.start', async ($, e, next) => {
    // State outlives a hot reload but the code that reads it does not, so PR and worktree state saved by an older
    // version can be in a shape this one doesn't expect. These are caches and a transient box, and a reload fires
    // session.start again, so each load starts them fresh. First, so a failure further down can't skip it.
    await update($, pickerOpen, () => false)
    await update($, openWorktree, () => null)
    await update($, worktreeDetails, () => ({}))
    await update($, prDetails, () => ({}))
    await update($, prs, () => ({}))
    await update($, openProject, () => null)
    await update($, projectTasks, () => ({}))
    await update($, consolidation, () => null)
    await update($, closing, () => null)
    await update($, newProject, () => null)

    await $.command.register(COMMAND_SPEC)
    await $.tool.register({
      name: WORKTREE_TOOL,
      description:
        "Creates a git worktree for <repo> on <branch>, following that repo's existing worktree " +
        'convention. Use this instead of branching in the primary checkout whenever a change needs isolation.',
      inputSchema: {
        type: 'object',
        properties: {
          repo: { type: 'string', description: 'Repo name as listed in the workspace map.' },
          branch: { type: 'string', description: 'Branch to check out (created if it does not exist locally).' },
        },
        required: ['repo', 'branch'],
      },
      isDeferred: false,
    })

    const refresh = async () => {
      const root = opts.workspaceRoot || (await $.session.root())
      const deps: Deps = {
        exec: (argv, cwd, env) => $.process.run(argv, { cwd, env }),
        readFile: path => $.fs.read(path).then(t => (typeof t === 'string' ? t : null)).catch(() => null),
        listDir: path =>
          $.fs
            .list(path)
            .then(entries => entries.map(entry => ({ name: entry.name, isDir: entry.kind === 'dir' })))
            .catch(() => []),
        join: joinPath,
        now: () => Date.now(),
      }
      const model = await discoverWorkspace(root, deps, { projectsScript: `${$.plugin.root}/scripts/projects.py` })
      await update($, workspaceModel, () => model)
      $.ui.status(statusLineFor(model, statusLine))

      const docPath = joinPath(root, docFile)
      const existing = await deps.readFile(docPath)
      const plan = planDocSyncFor(model, existing, autoSyncDoc)
      await update($, docStatus, () => plan.status)
      if (plan.write !== null) await $.fs.write(docPath, plan.write)
      else if (plan.status === 'stale') $.ui.toast(`${docFile} workspace map is stale — run /workspace sync-doc`)
    }

    await refresh()
    if (opts.paneAutoOpen) {
      // Opened unasked, so a narrow terminal keeps the pane waiting instead of seating it.
      const placed = await $.ui.open({ id: PANE_ID, title: 'Workspace' })
      if (!placed.isPlaced) $.ui.toast('Workspace pane is waiting for a wider terminal — widen it or run /workspace')
    }

    // Your open PRs load in the background: `gh` is a network call and `session.start` is awaited
    // before the first prompt. A lookup that fails is recorded in state for the pane to show; the
    // catch only guards the state plumbing, so a background rejection can't take the hooks worker down.
    $.clock.after(1, () => {
      void (async () => {
        const scanned = await read($, workspaceModel)
        for (const repo of scanned?.repos ?? []) {
          await update($, prs, cur => ({ ...cur, [repo.name]: loadingPrs }))
          const entry = await fetchPrs((argv, cwd) => $.process.run(argv, { cwd }), repo, Date.now())
          await update($, prs, cur => ({ ...cur, [repo.name]: entry }))
        }
      })().catch(() => undefined)
    })

    // A session.start timer's `$` is valid for the timer's whole life (until
    // cancelled or the module reloads) — the documented pattern for work that
    // outlives a single dispatch.
    $.clock.every(SAFETY_REFRESH_MS, () => {
      void refresh()
    })

    return next(e)
  })

  on('prompt.compose', async ($, e, next) => {
    const result = await next(e)
    const model = await read($, workspaceModel)
    const text = model ? buildWorkspaceSection(model) : null
    if (text === null) return result
    return { sections: [...result.sections, { id: SECTION_ID, text, scope: 'session' as const }] }
  })

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const command = String((e as { command?: string }).command ?? '')
    let repo: RepoInfo | null = null

    try {
      const model = await read($, workspaceModel)
      repo = model ? resolveRepoForCommand(command, model.repos) : null
      if (repo) {
        const verdict = evaluateGitCommand(command, repo, posture)
        if (verdict.action === 'deny') return { deny: `Workspaces: ${verdict.reason}` }
        if (verdict.action === 'warn') $.ui.toast(`Workspaces: ${verdict.reason}`)
      }
    } catch {
      repo = null // guardrail evaluation failed: fail open, run the command as asked
    }

    const ran = await next(e)

    if (repo && looksLikeGitMutation(command)) {
      try {
        const scanDeps: Deps = {
          exec: (argv, cwd) => $.process.run(argv, { cwd }),
          readFile: path => $.fs.read(path).then(t => (typeof t === 'string' ? t : null)).catch(() => null),
          listDir: () => Promise.resolve([]), // unused by scanRepo
          join: joinPath,
          now: () => Date.now(),
        }
        const freshRepo = await scanRepo(repo.name, repo.path, scanDeps, undefined, null)
        await update($, workspaceModel, current => replaceRepo(current, freshRepo))
        $.ui.status(statusLineFor(await read($, workspaceModel), statusLine))
      } catch {
        // best effort; a failed refresh never affects the tool call's own result
      }
    }

    return ran
  }).catch(($, e, next) => next(e)) // fail open: a guardrail bug must never block a real command

  on('tool.call', { tool: MCP_WORKTREE_TOOL }, async ($, e) => {
    const { repo: repoName, branch } = e as unknown as { repo: string; branch: string }
    const model = await read($, workspaceModel)
    const repo = model?.repos.find(r => r.name === repoName)
    if (!repo) {
      const known = model?.repos.map(r => r.name).join(', ') || '(none discovered yet)'
      return { deny: `Workspaces: unknown repo '${repoName}'. Known repos: ${known}` }
    }

    const outcome = await createWorktree(
      { exec: (argv, cwd) => $.process.run(argv, { cwd }) },
      repo,
      branch,
      model ? defaultWorktreeDir(model, repo) : undefined,
    )
    if (!outcome.ok) return { deny: `Workspaces: ${outcome.reason}` }

    const root = opts.workspaceRoot || (await $.session.root())
    const deps: Deps = {
      exec: (argv, cwd, env) => $.process.run(argv, { cwd, env }),
      readFile: path => $.fs.read(path).then(t => (typeof t === 'string' ? t : null)).catch(() => null),
      listDir: path =>
        $.fs
          .list(path)
          .then(entries => entries.map(entry => ({ name: entry.name, isDir: entry.kind === 'dir' })))
          .catch(() => []),
      join: joinPath,
      now: () => Date.now(),
    }
    const fresh = await discoverWorkspace(root, deps, { projectsScript: `${$.plugin.root}/scripts/projects.py` })
    await update($, workspaceModel, () => fresh)
    $.ui.status(statusLineFor(fresh, statusLine))

    return { result: { repo: repo.name, branch, path: outcome.path } }
  })
}
