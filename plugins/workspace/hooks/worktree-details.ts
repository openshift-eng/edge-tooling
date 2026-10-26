// What the worktree box shows that a scan doesn't hold: the upstream and ahead/behind, fresh changes, and the
// last commit, read from the worktree itself when the box opens. Like prs.ts, commands come in as an `Exec`,
// so nothing here touches `$` and plain unit tests cover it.

import { parseStatusPorcelain } from './discovery'
import type { ExecResult } from './discovery'
import type { Exec } from './prs'
import type { WorktreeDetails, WorktreeDetailsEntry } from './model'

const FIELD_SEPARATOR = '\x1f'

/** The commit in `git log -1` output formatted with unit separators, or null when it isn't one. */
export function parseLastCommit(stdout: string): WorktreeDetails['lastCommit'] {
  const [hash, message, author, date] = stdout.trim().split(FIELD_SEPARATOR)
  return hash && message !== undefined && author !== undefined && date
    ? { hash, message: message.trim(), author, date }
    : null
}

/** The latest commit, or null when the branch has none or git can't say; the box shows the rest regardless. */
async function readLastCommit(exec: Exec, path: string): Promise<WorktreeDetails['lastCommit']> {
  try {
    const result = await exec(['git', 'log', '-1', '--format=%h%x1f%B%x1f%an%x1f%cI'], path)
    return result.exitCode === 0 ? parseLastCommit(result.stdout) : null
  } catch {
    return null
  }
}

/** A worktree's upstream, ahead and behind, changes and last commit. Never throws: failures come back as an `error` entry. */
export async function fetchWorktreeDetails(exec: Exec, path: string, now: number): Promise<WorktreeDetailsEntry> {
  let status: ExecResult
  try {
    status = await exec(['git', 'status', '-sb'], path)
  } catch (err) {
    return { status: 'error', message: `Couldn't run git: ${err instanceof Error ? err.message : String(err)}` }
  }
  if (status.exitCode !== 0) {
    const firstLine = status.stderr.trim().split('\n')[0] ?? ''
    return { status: 'error', message: firstLine || `git exited ${status.exitCode}` }
  }

  const { upstream, ahead, behind, changes } = parseStatusPorcelain(status.stdout)
  return {
    status: 'ok',
    details: { upstream, ahead, behind, changes, lastCommit: await readLastCommit(exec, path) },
    fetchedAt: now,
  }
}
