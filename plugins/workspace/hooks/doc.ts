// Keeps a managed block inside the workspace-root AGENTS.md/CLAUDE.md in sync
// with the discovery model. Pure string operations: never touches anything
// outside the markers, so a hand-authored doc is never clobbered.

import type { DocStatus, RepoInfo, WorkspaceModel } from './model'
import { prTarget } from './discovery'

export const BEGIN = '<!-- workspaces:begin -->'
export const END = '<!-- workspaces:end -->'

/**
 * The markers an earlier version of the mod, called multi-repo, wrote into people's docs. A block between
 * them is still ours to replace, in place, so renaming the mod doesn't leave a second block behind.
 */
const LEGACY_MARKERS = [{ begin: '<!-- multi-repo:begin -->', end: '<!-- multi-repo:end -->' }] as const

/** Where the managed block is in `text`, from its begin marker to the end of its end marker, under either name. */
function findBlock(text: string): { start: number; end: number } | null {
  for (const { begin, end } of [{ begin: BEGIN, end: END }, ...LEGACY_MARKERS]) {
    const start = text.indexOf(begin)
    const stop = text.indexOf(end)
    if (start !== -1 && stop !== -1 && stop >= start) return { start, end: stop + end.length }
  }
  return null
}

/** Renders the managed block's full text (markers included) from the model. */
export function renderManagedBlock(repos: RepoInfo[]): string {
  const header = '| Repo | Path | Role | Push to | PRs land on |'
  const divider = '|---|---|---|---|---|'
  const rows = repos.map(r => {
    const target = prTarget(r.remotes)
    const primary = r.remotes.find(x => x.role === 'primary')?.name ?? '—'
    const canonical = target?.name ?? primary
    const role = (r.role ?? '—').replace(/\|/g, '\\|')
    return `| ${r.name} | ${r.path} | ${role} | ${primary} | ${canonical} |`
  })

  return [
    BEGIN,
    '<!-- Managed by the workspaces mod. Edit outside these markers — this block is regenerated from live discovery. -->',
    '',
    header,
    divider,
    ...rows,
    '',
    END,
  ].join('\n')
}

/**
 * Merges a freshly rendered block into existing doc text. Replaces the
 * content between the markers in place; appends a new block (with a blank
 * line separator) when no markers are present yet; starts a fresh doc when
 * there's no existing text at all.
 */
export function mergeManagedBlock(existing: string | null, block: string): string {
  if (existing === null || existing.trim() === '') {
    return `${block}\n`
  }

  const found = findBlock(existing)
  if (found === null) {
    const needsBlankLine = !existing.endsWith('\n\n')
    const separator = existing.endsWith('\n') ? (needsBlankLine ? '\n' : '') : '\n\n'
    return `${existing}${separator}${block}\n`
  }

  return `${existing.slice(0, found.start)}${block}${existing.slice(found.end)}`
}

/** True when the doc has no managed block yet, or its block no longer matches a fresh render. */
export function isBlockStale(existing: string | null, freshBlock: string): boolean {
  if (existing === null) return true
  const found = findBlock(existing)
  if (found === null) return true
  return existing.slice(found.start, found.end) !== freshBlock
}

export type DocPlan = { status: DocStatus; write: string | null }

/**
 * Decides what a refresh should do to the root doc, from already-fetched
 * inputs: write it (when auto-sync is on), or just report drift
 * (propose-by-default). Pure — every branch is a plain unit test.
 */
export function planDocSync(repos: RepoInfo[], existing: string | null, autoSyncDoc: boolean): DocPlan {
  if (repos.length === 0) return { status: null, write: null }
  const block = renderManagedBlock(repos)
  if (!isBlockStale(existing, block)) return { status: 'synced', write: null }
  if (autoSyncDoc) return { status: 'synced', write: mergeManagedBlock(existing, block) }
  return { status: existing === null ? 'missing' : 'stale', write: null }
}

/**
 * `planDocSync` for a scanned workspace. A single repo has no map to keep, and the root doc is the
 * project's own tracked file, so nothing is ever planned for it: no status, no write.
 */
export function planDocSyncFor(model: WorkspaceModel, existing: string | null, autoSyncDoc: boolean): DocPlan {
  return model.mode === 'single-repo' ? { status: null, write: null } : planDocSync(model.repos, existing, autoSyncDoc)
}
