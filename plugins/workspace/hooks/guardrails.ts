// Flags git commands that look like a mistake given the workspace's discovered
// remote roles: pushing straight to the canonical/upstream remote, or
// committing on a repo's default branch. Pure matching over a command string
// and a repo's classified remotes — no `$`, so it's unit-testable directly.

import type { RepoInfo } from './model'
import { prTarget } from './discovery'

export type GuardrailPosture = 'off' | 'warn' | 'deny'

export type GuardrailVerdict =
  | { action: 'allow' }
  | { action: 'warn'; reason: string }
  | { action: 'deny'; reason: string }

/**
 * Evaluates one shell command against one repo's known state. `repo` should
 * be the repo whose path the command ran in (resolved by the caller from the
 * tool call's cwd); when no repo matches, callers should skip evaluation
 * entirely rather than call this with a guess.
 */
export function evaluateGitCommand(command: string, repo: RepoInfo, posture: GuardrailPosture): GuardrailVerdict {
  if (posture === 'off') return { action: 'allow' }

  const pushArgs = matchSubcommand(command, 'push')
  if (pushArgs !== null) {
    const target = prTarget(repo.remotes)
    const primary = repo.remotes.find(r => r.role === 'primary')?.name
    const valueOptions = new Set(['-o', '--push-option', '--receive-pack', '--exec'])
    let positionalRepo: string | undefined
    let repoOption: string | undefined

    for (let i = 0; i < pushArgs.length; i++) {
      const a = pushArgs[i]!

      if (a === '--') {
        positionalRepo ??= pushArgs[i + 1]
        break
      }
      if (valueOptions.has(a)) {
        i++
        continue
      }
      // --repo supplies the destination itself, rather than an unrelated option value.
      if (a === '--repo' || a.startsWith('--repo=')) {
        repoOption = a === '--repo' ? pushArgs[++i] : a.slice('--repo='.length)
        continue
      }

      if (!a.startsWith('-')) positionalRepo ??= a
    }

    const remoteArg = positionalRepo ?? repoOption
    if (remoteArg && target && target.role === 'canonical' && remoteArg === target.name) {
      const reason = `${repo.name}: pushing straight to '${remoteArg}' (PRs land there) — push to '${primary ?? 'your remote'}' and open a PR instead.`
      return posture === 'deny' ? { action: 'deny', reason } : { action: 'warn', reason }
    }
  }

  if (matchSubcommand(command, 'commit') !== null) {
    if (repo.defaultBranch && repo.branch && repo.branch === repo.defaultBranch) {
      return {
        action: 'warn',
        reason: `${repo.name}: committing directly on '${repo.defaultBranch}' — consider a branch or worktree instead.`,
      }
    }
  }

  return { action: 'allow' }
}

/** Finds `git <subcommand> ...` in a (possibly compound) shell command and returns its args. */
function matchSubcommand(command: string, subcommand: string): string[] | null {
  const pattern = new RegExp(`(?:^|[;&|]\\s*)git\\s+${subcommand}\\b([^|&;]*)`)
  const match = pattern.exec(command)
  const captured = match?.[1]
  if (captured === undefined) return null
  const trimmed = captured.trim()
  return trimmed.length > 0 ? trimmed.split(/\s+/) : []
}

/** Matches a repo in the workspace to a shell command, by `-C <path>`, a leading `cd`, or a path mention. */
export function resolveRepoForCommand<R extends { path: string }>(command: string, repos: readonly R[]): R | null {
  const explicit = /(?:-C\s+|cd\s+)(\S+)/.exec(command)?.[1]?.replace(/^['"]|['"]$/g, '')
  if (explicit) {
    const found = repos.find(r => explicit === r.path || explicit.startsWith(`${r.path}/`) || r.path.endsWith(explicit))
    if (found) return found
  }
  return repos.find(r => command.includes(r.path)) ?? null
}

/** Whether a Bash command looks like it mutated git state worth a rescan. */
export function looksLikeGitMutation(command: string): boolean {
  return /\bgit\s+(push|commit|merge|rebase|checkout|switch|branch|worktree|pull|fetch|reset|cherry-pick)\b/.test(command)
}
