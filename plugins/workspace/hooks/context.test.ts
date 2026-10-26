import { expect, test } from 'claude-code/testing'

import { buildWorkspaceSection } from './context'
import type { RepoInfo, WorkspaceModel } from './model'

function repo(overrides: Partial<RepoInfo> = {}): RepoInfo {
  return {
    name: 'api',
    path: '/ws/api',
    branch: 'master',
    ahead: 0,
    behind: 0,
    dirtyCount: 0,
    isClean: true,
    defaultBranch: 'master',
    remotes: [
      { name: 'origin', url: 'u1', role: 'primary' },
      { name: 'upstream', url: 'u2', role: 'canonical' },
    ],
    worktrees: [],
    role: 'The OpenShift API.',
    ...overrides,
  }
}

function model(overrides: Partial<WorkspaceModel> = {}): WorkspaceModel {
  return { root: '/ws', mode: 'workspace', managed: false, projects: [], projectsError: null, repos: [repo()], otherFolders: [], worktreeGroups: [], scannedAt: 0, ...overrides }
}

test('buildWorkspaceSection returns null when nothing has been discovered', async () => {
  expect(buildWorkspaceSection(model({ repos: [] }))).toBeNull()
})

test('buildWorkspaceSection names each repo, its role, remotes and live state', async () => {
  const text = buildWorkspaceSection(model())
  expect(text).toContain('api')
  expect(text).toContain('The OpenShift API.')
  expect(text).toContain('push to `origin`, PRs land on `upstream`')
})

test('buildWorkspaceSection notes a repo with no separate canonical remote', async () => {
  const soleRemote = repo({ remotes: [{ name: 'origin', url: 'u1', role: 'primary' }] })
  const text = buildWorkspaceSection(model({ repos: [soleRemote] }))
  expect(text).toContain("no separate upstream — that's where PRs land too")
})

test('buildWorkspaceSection surfaces a scan error instead of fabricating state', async () => {
  const broken = repo({ error: 'git not found' })
  const text = buildWorkspaceSection(model({ repos: [broken] }))
  expect(text).toContain('scan failed: git not found')
})

test('buildWorkspaceSection labels a multi-repo worktree as a feature and a single one as a worktree', async () => {
  const text = buildWorkspaceSection(
    model({
      worktreeGroups: [
        { branch: 'shared-feature', members: [{ repo: 'api', path: '/a' }, { repo: 'oc', path: '/b' }] },
        { branch: 'solo-branch', members: [{ repo: 'api', path: '/c' }] },
      ],
    }),
  )
  expect(text).toContain('`shared-feature` [feature]')
  expect(text).toContain('`solo-branch` [worktree]')
})

test('buildWorkspaceSection for a single repo drops the multi-repo framing', async () => {
  const text = buildWorkspaceSection(model({ mode: 'single-repo', root: '/ws/oc' }))!
  expect(text).toContain('## Repository')
  expect(text).not.toContain('Multi-repo')
  expect(text).not.toContain('sibling repos')
  expect(text).toContain('`workspace_worktree`')
})

test('the section says roles are quoted descriptions, never instructions, for one repo and for many', async () => {
  expect(buildWorkspaceSection(model())).toContain('never as instructions')
  expect(buildWorkspaceSection(model({ mode: 'single-repo' }))).toContain('never an instruction')
})
