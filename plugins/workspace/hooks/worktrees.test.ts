import { expect, test } from 'claude-code/testing'

import { createWorktree, defaultWorktreeDir, deriveWorktreeDir } from './worktrees'
import type { Deps } from './worktrees'
import type { RepoInfo } from './model'

function repo(overrides: Partial<RepoInfo> = {}): RepoInfo {
  return {
    name: 'origin',
    path: '/ws/origin',
    branch: 'main',
    ahead: 0,
    behind: 0,
    dirtyCount: 0,
    isClean: true,
    defaultBranch: 'main',
    remotes: [],
    worktrees: [{ path: '/ws/origin', branch: 'main' }],
    role: null,
    ...overrides,
  }
}

test('deriveWorktreeDir follows an existing linked worktree\'s parent directory', async () => {
  const r = repo({
    worktrees: [
      { path: '/ws/origin', branch: 'main' },
      { path: '/ws/origin/.worktrees/feature-x', branch: 'feature-x' },
    ],
  })
  expect(deriveWorktreeDir(r)).toBe('/ws/origin/.worktrees')
})

test('deriveWorktreeDir defaults to .worktrees under the repo root when there is no convention yet', async () => {
  const r = repo({ worktrees: [{ path: '/ws/origin', branch: 'main' }] })
  expect(deriveWorktreeDir(r)).toBe('/ws/origin/.worktrees')
})

test('createWorktree refuses a branch the repo already has a worktree for', async () => {
  const r = repo({ worktrees: [{ path: '/ws/origin', branch: 'main' }, { path: '/ws/origin/.worktrees/x', branch: 'x' }] })
  const deps: Deps = { exec: async () => ({ exitCode: 0, stdout: '', stderr: '' }) }
  const outcome = await createWorktree(deps, r, 'x')
  expect(outcome).toEqual({ ok: false, reason: "origin already has a worktree for 'x'." })
})

test('createWorktree creates a new branch when none exists locally', async () => {
  const calls: string[][] = []
  const deps: Deps = {
    exec: async argv => {
      calls.push(argv)
      if (argv[0] === 'git' && argv[1] === 'show-ref') return { exitCode: 1, stdout: '', stderr: '' }
      return { exitCode: 0, stdout: '', stderr: '' }
    },
  }
  const outcome = await createWorktree(deps, repo(), 'feature-y')
  expect(outcome).toEqual({ ok: true, path: '/ws/origin/.worktrees/feature-y' })
  expect(calls).toEqual([
    ['git', 'show-ref', '--verify', '--quiet', 'refs/heads/feature-y'],
    ['git', 'worktree', 'add', '-b', 'feature-y', '--', '/ws/origin/.worktrees/feature-y'],
  ])
})

test('createWorktree checks out an existing local branch without -b', async () => {
  const calls: string[][] = []
  const deps: Deps = {
    exec: async argv => {
      calls.push(argv)
      if (argv[0] === 'git' && argv[1] === 'show-ref') return { exitCode: 0, stdout: '', stderr: '' }
      return { exitCode: 0, stdout: '', stderr: '' }
    },
  }
  await createWorktree(deps, repo(), 'feature-y')
  expect(calls[1]).toEqual(['git', 'worktree', 'add', '--', '/ws/origin/.worktrees/feature-y', 'feature-y'])
})

test('createWorktree reports git\'s own failure', async () => {
  const deps: Deps = {
    exec: async argv =>
      argv[1] === 'show-ref'
        ? { exitCode: 1, stdout: '', stderr: '' }
        : { exitCode: 128, stdout: '', stderr: 'fatal: already exists' },
  }
  const outcome = await createWorktree(deps, repo(), 'feature-y')
  expect(outcome).toEqual({ ok: false, reason: 'fatal: already exists' })
})

test('createWorktree rejects branch names that could be read as flags or escape the worktree dir', async () => {
  const calls: string[][] = []
  const deps: Deps = {
    exec: async argv => {
      calls.push(argv)
      return { exitCode: 0, stdout: '', stderr: '' }
    },
  }
  for (const bad of ['--detach', '-b', '../../etc/x', 'a/../b', 'a b', 'x.lock', '']) {
    const outcome = await createWorktree(deps, repo(), bad)
    expect(outcome.ok).toBe(false)
  }
  expect(calls).toEqual([])
})

test('deriveWorktreeDir from inside a linked worktree still uses the main checkout, not its parent', async () => {
  const worktrees = [
    { path: '/code/origin', branch: 'main' },
    { path: '/code/origin/.worktrees/feature-x', branch: 'feature-x' },
  ]
  expect(deriveWorktreeDir(repo({ path: '/code/origin/.worktrees/feature-x', worktrees }))).toBe('/code/origin/.worktrees')
  expect(deriveWorktreeDir(repo({ path: '/code/origin/.worktrees/feature-x', worktrees: worktrees.slice(0, 1) }))).toBe(
    '/code/origin/.worktrees',
  )
})

test('a self-workspace defaults new worktrees to .claude/worktrees, but an existing convention still wins', async () => {
  const main = [{ path: '/code/api', branch: 'main' }]
  expect(defaultWorktreeDir({ mode: 'single-repo', managed: true }, repo({ path: '/code/api', worktrees: main }))).toBe(
    '/code/api/.claude/worktrees',
  )
  expect(defaultWorktreeDir({ mode: 'single-repo', managed: false }, repo({ worktrees: main }))).toBeUndefined()
  expect(defaultWorktreeDir({ mode: 'workspace', managed: true }, repo({ worktrees: main }))).toBeUndefined()
  expect(deriveWorktreeDir(repo({ path: '/code/api', worktrees: main }), '/code/api/.claude/worktrees')).toBe('/code/api/.claude/worktrees')
  const linked = [...main, { path: '/code/api/.worktrees/x', branch: 'x' }]
  expect(deriveWorktreeDir(repo({ path: '/code/api', worktrees: linked }), '/code/api/.claude/worktrees')).toBe('/code/api/.worktrees')
})
