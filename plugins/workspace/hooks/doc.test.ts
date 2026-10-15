import { expect, test } from 'claude-code/testing'

import { BEGIN, END, isBlockStale, mergeManagedBlock, planDocSync, planDocSyncFor, renderManagedBlock } from './doc'
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

test('renderManagedBlock includes the markers and one row per repo', async () => {
  const block = renderManagedBlock([repo()])
  expect(block.startsWith(BEGIN)).toBe(true)
  expect(block.endsWith(END)).toBe(true)
  expect(block).toContain('| api | /ws/api | The OpenShift API. | origin | upstream |')
})

test('mergeManagedBlock starts a fresh doc when there is none yet', async () => {
  const block = renderManagedBlock([repo()])
  expect(mergeManagedBlock(null, block)).toBe(`${block}\n`)
})

test('mergeManagedBlock appends when the doc exists but has no markers', async () => {
  const block = renderManagedBlock([repo()])
  const existing = '# My workspace\n\nSome hand-written notes.\n'
  const merged = mergeManagedBlock(existing, block)
  expect(merged.startsWith(existing)).toBe(true)
  expect(merged).toContain(block)
})

test('mergeManagedBlock replaces only the content between markers, leaving hand-authored text untouched', async () => {
  const before = '# My workspace\n\nHand-authored intro.\n\n'
  const after = '\n\nHand-authored outro.\n'
  const oldBlock = renderManagedBlock([repo({ name: 'old-repo' })])
  const existing = `${before}${oldBlock}${after}`
  const newBlock = renderManagedBlock([repo({ name: 'new-repo' })])
  const merged = mergeManagedBlock(existing, newBlock)
  expect(merged).toBe(`${before}${newBlock}${after}`)
  expect(merged).toContain('Hand-authored intro.')
  expect(merged).toContain('Hand-authored outro.')
  expect(merged).not.toContain('old-repo')
})

test('isBlockStale is true with no doc, true when the block differs, false when it matches', async () => {
  const block = renderManagedBlock([repo()])
  expect(isBlockStale(null, block)).toBe(true)
  expect(isBlockStale('# doc with no block', block)).toBe(true)
  expect(isBlockStale(`before\n${block}\nafter`, block)).toBe(false)
})

test('planDocSync: no repos means nothing to do', async () => {
  expect(planDocSync([], null, false)).toEqual({ status: null, write: null })
})

test('planDocSync: propose-by-default reports drift without writing', async () => {
  const plan = planDocSync([repo()], null, false)
  expect(plan.status).toBe('missing')
  expect(plan.write).toBeNull()
})

test('planDocSync: stale against an existing doc reports "stale", not "missing"', async () => {
  const plan = planDocSync([repo()], '# doc with no block', false)
  expect(plan.status).toBe('stale')
  expect(plan.write).toBeNull()
})

test('planDocSync: auto-sync writes and reports synced', async () => {
  const plan = planDocSync([repo()], null, true)
  expect(plan.status).toBe('synced')
  expect(plan.write).not.toBeNull()
})

test('planDocSync: already in sync writes nothing', async () => {
  const block = renderManagedBlock([repo()])
  const plan = planDocSync([repo()], block, false)
  expect(plan.status).toBe('synced')
  expect(plan.write).toBeNull()
})

// What an earlier version of the mod, when it was called multi-repo, wrote into people's docs.
const LEGACY_BEGIN = '<!-- multi-repo:begin -->'
const LEGACY_END = '<!-- multi-repo:end -->'
const LEGACY_BLOCK = [
  LEGACY_BEGIN,
  '<!-- Managed by the multi-repo mod. Edit outside these markers — this block is regenerated from live discovery. -->',
  '',
  '| Repo | Path | Role | Push to | PRs land on |',
  '|---|---|---|---|---|',
  '| api | /ws/api | The OpenShift API. | origin | upstream |',
  '',
  LEGACY_END,
].join('\n')

test('the managed block is marked with the mod\'s new name', async () => {
  expect(BEGIN).toBe('<!-- workspaces:begin -->')
  expect(END).toBe('<!-- workspaces:end -->')
  const block = renderManagedBlock([repo()])
  expect(block).toContain('Managed by the workspaces mod')
  expect(block).not.toContain('multi-repo')
})

test('mergeManagedBlock replaces a block written under the old name in place, instead of adding a second one', async () => {
  const before = '# My workspace\n\nHand-authored intro.\n\n'
  const after = '\n\nHand-authored outro.\n'
  const block = renderManagedBlock([repo({ name: 'new-repo' })])

  const merged = mergeManagedBlock(`${before}${LEGACY_BLOCK}${after}`, block)

  expect(merged).toBe(`${before}${block}${after}`)
  expect(merged).not.toContain('multi-repo')
  expect(merged.split(BEGIN)).toHaveLength(2) // exactly one block
})

test('a block written under the old name counts as stale, so the nudge to sync it appears', async () => {
  const fresh = renderManagedBlock([repo()])
  expect(isBlockStale(`intro\n${LEGACY_BLOCK}\n`, fresh)).toBe(true)
})

test('planDocSync proposes moving an old-name block, and does it under auto-sync', async () => {
  const proposed = planDocSync([repo()], `intro\n${LEGACY_BLOCK}\n`, false)
  expect(proposed).toEqual({ status: 'stale', write: null })

  const done = planDocSync([repo()], `intro\n${LEGACY_BLOCK}\n`, true)
  expect(done.status).toBe('synced')
  expect(done.write).toContain(BEGIN)
  expect(done.write).not.toContain('multi-repo')
  expect(done.write?.startsWith('intro\n')).toBe(true)
})

test('planDocSyncFor plans nothing for a single repo, even with auto-sync on', async () => {
  const single: WorkspaceModel = {
    root: '/ws/api', mode: 'single-repo', managed: false, projects: [], projectsError: null, repos: [repo()], otherFolders: [], worktreeGroups: [], scannedAt: 0,
  }
  expect(planDocSyncFor(single, null, true)).toEqual({ status: null, write: null })
  expect(planDocSyncFor({ ...single, mode: 'workspace' }, null, true).write).not.toBeNull()
})
