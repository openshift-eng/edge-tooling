// Discovers the workspace: which sibling directories are git repos, their
// branch/remote/worktree state, and their role (from their own docs and the
// workspace-root doc). Every git/filesystem access goes through the `Deps`
// interface so this module is unit-testable with fakes — no `$` in sight.

import type { Changes, RemoteInfo, RemoteRole, RepoInfo, WorktreeEntry, WorkspaceModel } from './model'
import { loadProjects } from './projects'

export type ExecResult = { exitCode: number; stdout: string; stderr: string }

export type Deps = {
  exec: (argv: string[], cwd: string, env?: Record<string, string>) => Promise<ExecResult>
  readFile: (path: string) => Promise<string | null> // null when missing
  listDir: (path: string) => Promise<{ name: string; isDir: boolean }[]>
  join: (...parts: string[]) => string
  now: () => number
}

export type RemoteConvention = {
  /** Remote name pushed to day to day; defaults to the current branch's tracked remote, then "origin". */
  primaryRemote?: string
  /** Remote name PRs land on; defaults to "upstream" when present. */
  canonicalRemote?: string
}

export type DiscoveryOptions = {
  /** Per-repo overrides, keyed by repo directory name. */
  remoteConventions?: Record<string, RemoteConvention>
  /** Absolute path of `scripts/projects.py`; without it a managed workspace is listed with no projects. */
  projectsScript?: string
}

const DOC_CANDIDATES = ['CLAUDE.md', 'AGENTS.md', 'README.md']

type RepoScan = Pick<WorkspaceModel, 'root' | 'mode' | 'repos' | 'otherFolders' | 'worktreeGroups' | 'scannedAt'>

export async function discoverWorkspace(
  root: string,
  deps: Deps,
  options: DiscoveryOptions = {},
): Promise<WorkspaceModel> {
  // A workspace set up by the workspace skills has a dev-env.yaml at its root and its projects under it.
  const managed = (await deps.readFile(deps.join(root, 'dev-env.yaml'))) !== null
  const scan = await discoverRepos(root, deps, options, managed)
  if (!managed) return { ...scan, managed, projects: [], projectsError: null }
  const { projects, error } = options.projectsScript
    ? await loadProjects(deps.exec, options.projectsScript, root)
    : { projects: [], error: null }
  return { ...scan, managed, projects, projectsError: error }
}

async function discoverRepos(root: string, deps: Deps, options: DiscoveryOptions, managed: boolean): Promise<RepoScan> {
  // A managed multi-repo workspace keeps its repos under `repos/`, beside `projects/` and `domains/`.
  // That layout wins over everything below, even when the workspace root happens to be a repo itself.
  const reposDir = deps.join(root, 'repos')
  const nested = managed ? (await deps.listDir(reposDir)).some(e => e.isDir && !e.name.startsWith('.')) : false
  const scanDir = nested ? reposDir : root

  // A root that is itself the top of a git repo is one project, unless independent repos live under it
  // (a meta-repo with cloned siblings). A submodule is a tracked part of the repo, so it doesn't count.
  if (!nested && (await gitRepoRoot(deps, root)) !== null && !(await holdsIndependentRepos(deps, root))) {
    const repo = await scanRepo(baseName(root), root, deps, options.remoteConventions?.[baseName(root)], null)
    const worktreeGroups = groupWorktrees(
      linkedWorktrees(repo).map(w => ({ repo: repo.name, path: w.path, branch: w.branch ?? `(detached @ ${w.path})` })),
    )
    return { root, mode: 'single-repo', repos: [repo], otherFolders: [], worktreeGroups, scannedAt: deps.now() }
  }

  const rootDocText = await readFirstExisting(deps, root, ['AGENTS.md', 'CLAUDE.md'])
  const entries = await deps.listDir(scanDir)
  const candidates = entries
    .filter(e => e.isDir && !e.name.startsWith('.'))
    .sort((a, b) => a.name.localeCompare(b.name))

  const scanned: { repo: RepoInfo; toplevel: string }[] = []
  const otherFolders: string[] = []
  for (const entry of candidates) {
    const path = deps.join(scanDir, entry.name)
    const toplevel = await gitRepoRoot(deps, path)
    if (toplevel === null) {
      otherFolders.push(entry.name)
      continue
    }
    const repo = await scanRepo(entry.name, path, deps, options.remoteConventions?.[entry.name], rootDocText)
    scanned.push({ repo, toplevel })
  }

  // A sibling folder that is a linked worktree of another scanned repo belongs under that repo (its
  // `git worktree list` already names the folder), not beside it. Both paths come from git, so the
  // comparison holds even when the workspace root is reached through a symlink.
  const toplevels = new Set(scanned.map(s => s.toplevel))
  const repos = scanned
    .filter(({ repo, toplevel }) => {
      const main = repo.worktrees[0]?.path
      return main === undefined || main === toplevel || !toplevels.has(main)
    })
    .map(s => s.repo)

  const worktreeGroups = groupWorktrees(
    repos.flatMap(r =>
      linkedWorktrees(r).map(w => ({ repo: r.name, path: w.path, branch: w.branch ?? `(detached @ ${w.path})` })),
    ),
  )

  return { root, mode: 'workspace', repos, otherFolders, worktreeGroups, scannedAt: deps.now() }
}

/**
 * What changed in a worktree, or undefined when it can't be read (a directory that is gone, a worktree git
 * no longer has a checkout for): the pane then shows no breakdown, and the repo's scan goes on.
 */
async function readChanges(deps: Deps, worktreePath: string): Promise<Changes | undefined> {
  try {
    const result = await deps.exec(['git', 'status', '--porcelain'], worktreePath)
    return result.exitCode === 0 ? parseStatusPorcelain(result.stdout).changes : undefined
  } catch {
    return undefined
  }
}

/** Whether a folder under `root` is a git repo of its own that the root repo does not track as a submodule. */
async function holdsIndependentRepos(deps: Deps, root: string): Promise<boolean> {
  const children = (await deps.listDir(root)).filter(e => e.isDir && !e.name.startsWith('.'))
  const independent = await Promise.all(
    children.map(async child => {
      if ((await gitRepoRoot(deps, deps.join(root, child.name))) === null) return false
      const tracked = await deps.exec(['git', 'ls-files', '--stage', '--', child.name], root)
      return !(tracked.exitCode === 0 && tracked.stdout.startsWith('160000'))
    }),
  )
  return independent.some(Boolean)
}

/** The last segment of a path, ignoring a trailing slash. */
function baseName(path: string): string {
  const trimmed = path.replace(/\/+$/, '')
  return trimmed.slice(trimmed.lastIndexOf('/') + 1) || path
}

/** The worktrees of a repo other than the one its own folder is. */
export function linkedWorktrees(repo: Pick<RepoInfo, 'path' | 'worktrees'>): WorktreeEntry[] {
  return repo.worktrees.filter(w => w.path !== repo.path)
}

/**
 * The work tree root a folder is the top of, or null when it is not one. `--show-prefix` is empty
 * exactly at a work tree's root, so a plain folder that merely sits inside an enclosing repo is not
 * mistaken for a repo; outside any repo git exits non-zero.
 */
async function gitRepoRoot(deps: Deps, path: string): Promise<string | null> {
  const result = await deps.exec(['git', 'rev-parse', '--show-prefix', '--show-toplevel'], path)
  if (result.exitCode !== 0) return null
  const [prefix, toplevel] = result.stdout.split('\n')
  return prefix === '' && toplevel ? toplevel.trim() : null
}

/** Re-scans a single repo; used for the incremental refresh after a git-mutating tool call. */
export async function scanRepo(
  name: string,
  path: string,
  deps: Deps,
  convention: RemoteConvention | undefined,
  rootDocText: string | null,
): Promise<RepoInfo> {
  try {
    const [statusOut, remoteOut, worktreeOut] = await Promise.all([
      run(deps, ['git', 'status', '-sb'], path),
      run(deps, ['git', 'remote', '-v'], path),
      run(deps, ['git', 'worktree', 'list', '--porcelain'], path),
    ])

    const status = parseStatusPorcelain(statusOut)
    const trackedRemote = status.trackedRemote
    const remotes = classifyRemotes(parseRemotes(remoteOut), trackedRemote, convention)
    const worktrees = await Promise.all(
      parseWorktreeList(worktreeOut).map(async w => {
        const changes = w.path === path ? status.changes : await readChanges(deps, w.path)
        return changes === undefined ? w : { ...w, changes }
      }),
    )
    const primary = remotes.find(r => r.role === 'primary')?.name ?? null
    const defaultBranch = primary ? await detectDefaultBranch(deps, path, primary) : null
    const role = await resolveRole(deps, path, name, rootDocText)

    return {
      name,
      path,
      branch: status.branch,
      ahead: status.ahead,
      behind: status.behind,
      dirtyCount: status.dirtyCount,
      changes: status.changes,
      isClean: status.dirtyCount === 0,
      defaultBranch,
      remotes,
      worktrees,
      role,
    }
  } catch (err) {
    return {
      name,
      path,
      branch: null,
      ahead: 0,
      behind: 0,
      dirtyCount: 0,
      isClean: true,
      defaultBranch: null,
      remotes: [],
      worktrees: [],
      role: null,
      error: err instanceof Error ? err.message : String(err),
    }
  }
}

async function run(deps: Deps, argv: string[], cwd: string): Promise<string> {
  const result = await deps.exec(argv, cwd)
  if (result.exitCode !== 0) {
    throw new Error(`${argv.join(' ')} failed in ${cwd}: ${result.stderr.trim() || `exit ${result.exitCode}`}`)
  }
  return result.stdout
}

// ---- status -sb parsing -----------------------------------------------

/**
 * Which kind of change a `git status --porcelain` entry is, from its two status letters (index, then work
 * tree): untracked and staged adds are new, a delete in either place is deleted, and everything else (edits,
 * renames, copies, type changes, conflicts) is changed.
 */
function classifyEntry(xy: string): keyof Changes {
  const [x = ' ', y = ' '] = xy
  if (xy === '??') return 'added'
  if (x === 'U' || y === 'U' || xy === 'AA' || xy === 'DD') return 'changed'
  if (x === 'A') return 'added'
  if (x === 'D' || y === 'D') return 'deleted'
  return 'changed'
}

export function parseStatusPorcelain(output: string): {
  branch: string | null
  trackedRemote: string | null
  ahead: number
  behind: number
  dirtyCount: number
  changes: Changes
  upstream: string | null
} {
  const lines = output.split('\n').filter(l => l.length > 0)
  const header = lines.find(l => l.startsWith('##'))
  let branch: string | null = null
  let trackedRemote: string | null = null
  let upstreamRef: string | null = null
  let ahead = 0
  let behind = 0

  if (header) {
    // "## main...origin/main [ahead 19, behind 2]" or "## main" (no upstream)
    const body = header.slice(2).trim()
    const [refPart, bracket] = splitOnce(body, ' [')
    const refSegments = refPart.split('...')
    const localBranch = refSegments[0] ?? refPart
    const upstream = refSegments[1]
    branch = localBranch === 'HEAD (no branch)' ? null : localBranch
    if (upstream) {
      upstreamRef = upstream
      const slash = upstream.indexOf('/')
      if (slash > 0) trackedRemote = upstream.slice(0, slash)
    }
    if (bracket) {
      const aheadMatch = /ahead (\d+)/.exec(bracket)
      const behindMatch = /behind (\d+)/.exec(bracket)
      if (aheadMatch) ahead = Number(aheadMatch[1])
      if (behindMatch) behind = Number(behindMatch[1])
    }
  }

  const changes: Changes = { changed: 0, added: 0, deleted: 0 }
  for (const line of lines) {
    if (!line.startsWith('##')) changes[classifyEntry(line.slice(0, 2))] += 1
  }
  const dirtyCount = changes.changed + changes.added + changes.deleted
  return { branch, trackedRemote, ahead, behind, dirtyCount, changes, upstream: upstreamRef }
}

function splitOnce(s: string, sep: string): [string, string | null] {
  const idx = s.indexOf(sep)
  return idx === -1 ? [s, null] : [s.slice(0, idx), s.slice(idx + sep.length).replace(/\]$/, '')]
}

// ---- remote -v parsing + classification --------------------------------

export function parseRemotes(output: string): { name: string; url: string }[] {
  const seen = new Map<string, string>()
  for (const line of output.split('\n')) {
    const match = /^(\S+)\s+(\S+)\s+\((fetch|push)\)$/.exec(line.trim())
    const name = match?.[1]
    const url = match?.[2]
    if (name !== undefined && url !== undefined) seen.set(name, url)
  }
  return [...seen.entries()].map(([name, url]) => ({ name, url }))
}

export function classifyRemotes(
  remotes: { name: string; url: string }[],
  trackedRemote: string | null,
  convention?: RemoteConvention,
): RemoteInfo[] {
  const primaryName = convention?.primaryRemote ?? trackedRemote ?? (hasRemote(remotes, 'origin') ? 'origin' : null)
  const canonicalCandidate = convention?.canonicalRemote ?? (hasRemote(remotes, 'upstream') ? 'upstream' : null)
  // A repo with no fork (origin IS the canonical repo) has no separate canonical remote.
  const canonicalName = canonicalCandidate && canonicalCandidate !== primaryName ? canonicalCandidate : null

  return remotes.map(r => {
    const role: RemoteRole = r.name === primaryName ? 'primary' : r.name === canonicalName ? 'canonical' : 'other'
    return { name: r.name, url: r.url, role }
  })
}

function hasRemote(remotes: { name: string }[], name: string): boolean {
  return remotes.some(r => r.name === name)
}

/** Where a push should land and where a PR should target, derived from classified remotes. */
export function prTarget(remotes: RemoteInfo[]): RemoteInfo | null {
  return remotes.find(r => r.role === 'canonical') ?? remotes.find(r => r.role === 'primary') ?? remotes[0] ?? null
}

// ---- worktree list --porcelain parsing ---------------------------------

export function parseWorktreeList(output: string): WorktreeEntry[] {
  const entries: WorktreeEntry[] = []
  let path: string | null = null
  let branch: string | null = null

  const flush = () => {
    if (path !== null) entries.push({ path, branch })
    path = null
    branch = null
  }

  for (const line of output.split('\n')) {
    if (line.startsWith('worktree ')) {
      flush()
      path = line.slice('worktree '.length).trim()
    } else if (line.startsWith('branch ')) {
      branch = line.slice('branch '.length).trim().replace(/^refs\/heads\//, '')
    } else if (line === 'detached') {
      branch = null
    } else if (line.trim() === '') {
      flush()
    }
  }
  flush()

  return entries
}

async function detectDefaultBranch(deps: Deps, path: string, primaryRemote: string): Promise<string | null> {
  try {
    const head = await deps.exec(['git', 'symbolic-ref', `refs/remotes/${primaryRemote}/HEAD`], path)
    if (head.exitCode === 0) {
      const ref = head.stdout.trim().replace(`refs/remotes/${primaryRemote}/`, '')
      if (ref) return ref
    }
  } catch {
    // fall through to the branch-listing fallback
  }
  try {
    const branches = await deps.exec(['git', 'branch', '-r'], path)
    if (branches.exitCode === 0) {
      for (const candidate of ['main', 'master']) {
        if (branches.stdout.includes(`${primaryRemote}/${candidate}`)) return candidate
      }
    }
  } catch {
    // best effort only
  }
  return null
}

// ---- worktree grouping (across repos, by branch) -----------------------

export function groupWorktrees(
  entries: { repo: string; path: string; branch: string }[],
): { branch: string; members: { repo: string; path: string }[] }[] {
  const byBranch = new Map<string, { repo: string; path: string }[]>()
  for (const e of entries) {
    const list = byBranch.get(e.branch) ?? []
    list.push({ repo: e.repo, path: e.path })
    byBranch.set(e.branch, list)
  }
  return [...byBranch.entries()]
    .map(([branch, members]) => ({ branch, members }))
    .sort((a, b) => b.members.length - a.members.length || a.branch.localeCompare(b.branch))
}

// ---- role text: from the repo's own docs and the workspace-root doc ----

export const MAX_ROLE_LENGTH = 200

/**
 * A role comes from a repo's own docs, which may be someone else's text, and it ends up in the system prompt
 * and in a doc Claude reads. Keep it to one short line of plain text: control characters and line breaks are
 * dropped, whitespace collapsed, and the length capped, so a README can describe a repo but not smuggle in
 * a block of instructions.
 */
export function sanitizeRole(text: string): string | null {
  const flat = text
    // eslint-disable-next-line no-control-regex
    .replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  if (flat === '') return null
  return flat.length > MAX_ROLE_LENGTH ? `${flat.slice(0, MAX_ROLE_LENGTH - 1).trimEnd()}…` : flat
}

async function resolveRole(deps: Deps, path: string, name: string, rootDocText: string | null): Promise<string | null> {
  const raw = await readRole(deps, path, name, rootDocText)
  return raw === null ? null : sanitizeRole(raw)
}

async function readRole(deps: Deps, path: string, name: string, rootDocText: string | null): Promise<string | null> {
  const fromRoot = rootDocText ? extractRepoSectionFromRootDoc(rootDocText, name) : null
  if (fromRoot) return fromRoot

  for (const file of DOC_CANDIDATES) {
    const text = await deps.readFile(deps.join(path, file))
    if (text) {
      const para = extractFirstParagraph(text)
      if (para) return para
    }
  }
  return null
}

async function readFirstExisting(deps: Deps, dir: string, names: string[]): Promise<string | null> {
  for (const name of names) {
    const text = await deps.readFile(deps.join(dir, name))
    if (text) return text
  }
  return null
}

/** The first paragraph of prose in a markdown doc: skips headings and blank lines. */
export function extractFirstParagraph(doc: string): string | null {
  const lines = doc.split('\n')
  const buffer: string[] = []
  for (const line of lines) {
    const trimmed = line.trim()
    if (trimmed === '' ) {
      if (buffer.length > 0) break
      continue
    }
    if (trimmed.startsWith('#') || trimmed.startsWith('<!--')) continue
    buffer.push(trimmed)
  }
  return buffer.length > 0 ? buffer.join(' ') : null
}

/**
 * Finds the per-repo section in the workspace-root doc and returns its first
 * paragraph. Matches a heading naming the repo, with or without backticks:
 * `### \`api\`` or `### api`.
 */
export function extractRepoSectionFromRootDoc(doc: string, repoName: string): string | null {
  const lines = doc.split('\n')
  const headingPattern = new RegExp(`^#{1,6}\\s+\`?${escapeRegExp(repoName)}\`?\\s*$`, 'i')
  const startIndex = lines.findIndex(l => headingPattern.test(l.trim()))
  if (startIndex === -1) return null

  const rest = lines.slice(startIndex + 1)
  const nextHeadingOffset = rest.findIndex(l => /^#{1,6}\s/.test(l.trim()))
  const section = (nextHeadingOffset === -1 ? rest : rest.slice(0, nextHeadingOffset)).join('\n')
  return extractFirstParagraph(section)
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}
