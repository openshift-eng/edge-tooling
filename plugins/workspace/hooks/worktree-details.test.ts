import { expect, test } from 'claude-code/testing'

import { fetchWorktreeDetails, parseLastCommit } from './worktree-details'
import type { Exec } from './prs'

const SEP = '\x1f'

test('parseLastCommit reads the hash, the whole message, the author and the date', async () => {
  expect(
    parseLastCommit(`abc1234${SEP}Fix the thing\n\nWith a longer explanation.\n${SEP}Jeff Roche${SEP}2026-10-07T19:52:19-04:00\n`),
  ).toEqual({
    hash: 'abc1234',
    message: 'Fix the thing\n\nWith a longer explanation.',
    author: 'Jeff Roche',
    date: '2026-10-07T19:52:19-04:00',
  })
})

test('parseLastCommit is null when there is no commit to read', async () => {
  expect(parseLastCommit('')).toBeNull()
  expect(parseLastCommit('\n')).toBeNull()
  expect(parseLastCommit('not a commit line')).toBeNull()
})

/** git answering `status -sb` and `log -1` as given, recording each call. */
const gitAnswering = (
  status: { exitCode: number; stdout: string; stderr?: string },
  log: { exitCode: number; stdout: string; stderr?: string } | 'throws',
) => {
  const calls: { argv: string[]; cwd: string }[] = []
  const exec: Exec = async (argv, cwd) => {
    calls.push({ argv, cwd })
    if (argv[1] === 'log') {
      if (log === 'throws') throw new Error('spawn git ENOENT')
      return { stderr: '', ...log }
    }
    return { stderr: '', ...status }
  }
  return { exec, calls }
}

const COMMIT = `abc1234${SEP}Fix the thing\n${SEP}Jeff Roche${SEP}2026-10-07T19:52:19-04:00\n`

test('fetchWorktreeDetails reads the upstream, ahead and behind, the changes and the last commit', async () => {
  const { exec, calls } = gitAnswering(
    { exitCode: 0, stdout: '## topology...origin/topology [ahead 2, behind 1]\n M a.go\n?? newdir/\n D old.go\n' },
    { exitCode: 0, stdout: COMMIT },
  )
  const entry = await fetchWorktreeDetails(exec, '/ws/api/.wt/topology', 99)
  expect(entry).toEqual({
    status: 'ok',
    fetchedAt: 99,
    details: {
      upstream: 'origin/topology',
      ahead: 2,
      behind: 1,
      changes: { changed: 1, added: 1, deleted: 1 },
      lastCommit: { hash: 'abc1234', message: 'Fix the thing', author: 'Jeff Roche', date: '2026-10-07T19:52:19-04:00' },
    },
  })
  expect(calls.every(call => call.cwd === '/ws/api/.wt/topology')).toBe(true)
  expect(calls.find(call => call.argv[1] === 'status')?.argv).toEqual(['git', 'status', '-sb'])
  expect(calls.find(call => call.argv[1] === 'log')?.argv).toEqual(['git', 'log', '-1', '--format=%h%x1f%B%x1f%an%x1f%cI'])
})

test('fetchWorktreeDetails has no upstream for a branch that tracks nothing, and a clean tree has no changes', async () => {
  const { exec } = gitAnswering({ exitCode: 0, stdout: '## scratch\n' }, { exitCode: 0, stdout: COMMIT })
  const entry = await fetchWorktreeDetails(exec, '/ws/x', 1)
  expect(entry.status === 'ok' && entry.details.upstream).toBeNull()
  expect(entry.status === 'ok' && entry.details.changes).toEqual({ changed: 0, added: 0, deleted: 0 })
})

test('fetchWorktreeDetails still answers, with no last commit, for a branch with no commits or a log that fails', async () => {
  const noCommits = gitAnswering(
    { exitCode: 0, stdout: '## main\n' },
    { exitCode: 128, stdout: '', stderr: "fatal: your current branch 'main' does not have any commits yet" },
  )
  expect((await fetchWorktreeDetails(noCommits.exec, '/ws/x', 1)).status === 'ok').toBe(true)
  const entry = await fetchWorktreeDetails(noCommits.exec, '/ws/x', 1)
  expect(entry.status === 'ok' && entry.details.lastCommit).toBeNull()

  const throwing = gitAnswering({ exitCode: 0, stdout: '## main\n' }, 'throws')
  const second = await fetchWorktreeDetails(throwing.exec, '/ws/x', 1)
  expect(second.status === 'ok' && second.details.lastCommit).toBeNull()
})

test('fetchWorktreeDetails reports git status failing, with the first line of its error', async () => {
  const { exec } = gitAnswering(
    { exitCode: 128, stdout: '', stderr: 'fatal: not a git repository\nmore' },
    { exitCode: 0, stdout: COMMIT },
  )
  expect(await fetchWorktreeDetails(exec, '/ws/gone', 1)).toEqual({ status: 'error', message: 'fatal: not a git repository' })
})

test('fetchWorktreeDetails reports git not running at all', async () => {
  const exec: Exec = async () => {
    throw new Error('spawn git ENOENT')
  }
  expect(await fetchWorktreeDetails(exec, '/ws/x', 1)).toEqual({
    status: 'error',
    message: "Couldn't run git: spawn git ENOENT",
  })
})
