// The `/workspace` command: opens the pane by default, `refresh` forces a
// rescan, `sync-doc` regenerates the managed block in the root doc.
//
// The actual rescan is written inline inside the `command.run` hook below,
// not factored into a helper that takes `$` as a parameter: the engine's
// static check requires `$` to appear only as `$.noun.method(...)` at its
// own call site (or as a direct argument to `update`/`read`), never passed
// through a plugin's own function. Only plain-data helpers (discovery.ts,
// doc.ts) are shared; the `$` wiring is necessarily local to each hook.

import { atom, update } from 'claude-code'
import type { CommandSpec, On } from 'claude-code'

import { discoverWorkspace } from './discovery'
import type { Deps } from './discovery'
import type { DocStatus, WorkspaceModel } from './model'
import { planDocSyncFor } from './doc'
import { PANE_ID, SINGLE_REPO_DOC_NOTE, statusLineFor } from './ui'

// Declared locally per the state-scan rule (see model.ts's header comment);
// the `{ plugin, key }` pair is what makes this the same slot register.tsx
// and ui.tsx read and write.
const workspaceModel = atom({ plugin: 'workspace', key: 'model' } as const, null as WorkspaceModel | null)
const docStatus = atom({ plugin: 'workspace', key: 'docStatus' } as const, null as DocStatus)

export const COMMAND_NAME = 'workspace'

export const COMMAND_SPEC: CommandSpec = {
  name: COMMAND_NAME,
  description: 'Show the workspace map; `refresh` rescans, `sync-doc` updates the root doc.',
  argumentHint: '[refresh|sync-doc]',
}

// The fullscreen layout docks a pane beside the transcript from this many columns.
export const MIN_PANE_COLUMNS = 110

/** Pure: whether the pane has room. The main-screen layout seats it inline above the prompt at any width. */
export function paneFitsTerminal(presentation: { isFullscreen: boolean; columns: number }): boolean {
  return !presentation.isFullscreen || presentation.columns >= MIN_PANE_COLUMNS
}

export type WorkspaceCommandArg = 'open' | 'refresh' | 'sync-doc'

/** Pure: what `/workspace <args>` means, independent of how it's carried out. */
export function parseWorkspaceCommandArg(args: string): WorkspaceCommandArg {
  const trimmed = args.trim()
  if (trimmed === 'refresh') return 'refresh'
  if (trimmed === 'sync-doc') return 'sync-doc'
  return 'open'
}

export type WorkspaceCommandConfig = {
  workspaceRoot?: string
  statusLine: boolean
  autoSyncDoc: boolean
  docFile: 'AGENTS.md' | 'CLAUDE.md'
}

export function registerWorkspaceCommandHandler(on: On, config: WorkspaceCommandConfig) {
  on('command.run', { command: COMMAND_NAME }, async ($, e) => {
    const action = parseWorkspaceCommandArg(e.args)

    if (action === 'open') {
      if (!paneFitsTerminal(e.presentation)) {
        $.ui.toast(`Terminal is too narrow for the workspace pane (${e.presentation.columns} columns, needs ${MIN_PANE_COLUMNS}) — widen it and run /workspace again`)
        return {}
      }
      await $.ui.open({ id: PANE_ID, title: 'Workspace' })
      return { text: 'Workspace pane opened.' }
    }

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

    const model = await discoverWorkspace(root, deps, { projectsScript: `${$.plugin.root}/scripts/projects.py` })
    await update($, workspaceModel, () => model)
    $.ui.status(statusLineFor(model, config.statusLine))

    const docPath = `${root}/${config.docFile}`
    const existing = await deps.readFile(docPath)

    if (action === 'refresh') {
      const plan = planDocSyncFor(model, existing, config.autoSyncDoc)
      await update($, docStatus, () => plan.status)
      if (plan.write !== null) await $.fs.write(docPath, plan.write)
      return {
        text:
          model.mode === 'single-repo'
            ? 'Repo rescanned.'
            : `Workspace rescanned: ${model.repos.length} git repos, ${model.otherFolders.length} non-git folders.`,
      }
    }

    // action === 'sync-doc': always write, regardless of autoSyncDoc.
    if (model.mode === 'single-repo') return { text: SINGLE_REPO_DOC_NOTE }

    const plan = planDocSyncFor(model, existing, true)
    await update($, docStatus, () => plan.status)

    if (plan.write === null) {
      return {
        text: model.repos.length === 0
          ? 'No repos discovered; nothing to write.'
          : `The workspace-map block in ${config.docFile} is already up to date.`,
      }
    }

    await $.fs.write(docPath, plan.write)
    return { text: `Updated the workspace-map block in ${config.docFile}.` }
  })
}
