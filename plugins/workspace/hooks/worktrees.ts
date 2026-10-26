// Automatic worktree creation: Claude calls the `workspace_worktree` tool
// (or the mod notices a request needs isolation) instead of branching in the
// primary checkout. The target directory follows whatever convention the
// repo's own worktrees already use; `deriveWorktreeDir` is pure so that
// convention-matching is unit-testable without touching git.

import type { ExecResult } from './discovery'
import type { RepoInfo, WorkspaceModel } from './model'

export type Deps = {
  exec: (argv: string[], cwd: string) => Promise<ExecResult>
}

/**
 * Infers where a new worktree for this repo should live: beside its existing
 * linked worktrees when there are any (same parent directory), or a
 * `.worktrees` directory under the main checkout otherwise. The main checkout
 * is git's first worktree, which is not the repo's own folder when that
 * folder is a linked worktree.
 */
export function deriveWorktreeDir(repo: Pick<RepoInfo, 'path' | 'worktrees'>, fallbackDir?: string): string {
  const main = repo.worktrees[0]?.path ?? repo.path
  const first = repo.worktrees.find(w => w.path !== main)
  if (first) {
    return dirname(first.path)
  }
  return fallbackDir ?? `${main}/.worktrees`
}

/**
 * Where a workspace's skills put a repo's first worktree: a self-workspace (the workspace root is the
 * repo, set up by the workspace skills) keeps them under `.claude/worktrees`, everything else under
 * `.worktrees`. Only a default: existing worktrees still decide first.
 */
export function defaultWorktreeDir(
  model: Pick<WorkspaceModel, 'mode' | 'managed'>,
  repo: Pick<RepoInfo, 'path' | 'worktrees'>,
): string | undefined {
  if (model.mode !== 'single-repo' || !model.managed) return undefined
  return `${repo.worktrees[0]?.path ?? repo.path}/.claude/worktrees`
}

function dirname(path: string): string {
  const idx = path.lastIndexOf('/')
  return idx <= 0 ? '/' : path.slice(0, idx)
}

export type CreateWorktreeResult = { ok: true; path: string } | { ok: false; reason: string }

// Conservative subset of git's ref-name rules: no leading '-' (flag smuggling), no '..' segments
// (the name is also joined into the worktree path), no whitespace or ref-syntax characters.
const SAFE_BRANCH = /^[A-Za-z0-9._][A-Za-z0-9._\/-]*$/

export function isSafeBranchName(branch: string): boolean {
  return SAFE_BRANCH.test(branch) && !branch.split('/').some(part => part === '..' || part === '' || part.endsWith('.lock')) && !branch.endsWith('.')
}

export async function createWorktree(
  deps: Deps,
  repo: RepoInfo,
  branch: string,
  fallbackDir?: string,
): Promise<CreateWorktreeResult> {
  if (!isSafeBranchName(branch)) {
    return { ok: false, reason: `'${branch}' is not a valid branch name.` }
  }
  if (repo.worktrees.some(w => w.branch === branch)) {
    return { ok: false, reason: `${repo.name} already has a worktree for '${branch}'.` }
  }

  const dir = deriveWorktreeDir(repo, fallbackDir)
  const path = `${dir}/${branch}`
  const hasLocalBranch = await branchExistsLocally(deps, repo.path, branch)
  const argv = hasLocalBranch ? ['git', 'worktree', 'add', '--', path, branch] : ['git', 'worktree', 'add', '-b', branch, '--', path]

  const result = await deps.exec(argv, repo.path)
  if (result.exitCode !== 0) {
    return { ok: false, reason: result.stderr.trim() || `git worktree add exited ${result.exitCode}` }
  }
  return { ok: true, path }
}

async function branchExistsLocally(deps: Deps, repoPath: string, branch: string): Promise<boolean> {
  const result = await deps.exec(['git', 'show-ref', '--verify', '--quiet', `refs/heads/${branch}`], repoPath)
  return result.exitCode === 0
}
