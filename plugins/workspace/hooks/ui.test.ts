import { expect, test } from 'claude-code/testing'

import {
  changeSegments,
  commitMessageText,
  checksLabel,
  formatAge,
  lastCommitText,
  prsNote,
  prStatusColor,
  prStatusLabel,
  refreshToast,
  repoOptionLabel,
  repoToggleText,
  renderPrDetailsMarkdown,
  renderWorktreeName,
  resolveSelectedRepo,
  statusLineFor,
  statusLineText,
  statusLineToggleLabel,
  statusSegments,
  tabLabel,
  trackingText,
  unresolvedLabel,
  workspaceName,
} from './ui'
import type { PrDetails, RepoInfo, WorkspaceModel } from './model'

function repo(overrides: Partial<RepoInfo> = {}): RepoInfo {
  return {
    name: 'oc',
    path: '/ws/oc',
    branch: 'main',
    ahead: 0,
    behind: 0,
    dirtyCount: 0,
    isClean: true,
    defaultBranch: 'main',
    remotes: [{ name: 'origin', url: 'u1', role: 'primary' }],
    worktrees: [],
    role: null,
    ...overrides,
  }
}

function model(overrides: Partial<WorkspaceModel> = {}): WorkspaceModel {
  return { root: '/ws', mode: 'workspace', managed: false, projects: [], projectsError: null, repos: [repo()], otherFolders: [], worktreeGroups: [], scannedAt: 0, ...overrides }
}

test('statusLineText is undefined with no repos discovered', async () => {
  expect(statusLineText(null)).toBeUndefined()
  expect(statusLineText(model({ repos: [] }))).toBeUndefined()
})

test('statusLineText reports dirty repos and ahead/behind arrows', async () => {
  const ahead = repo({ name: 'oc', ahead: 19, isClean: true })
  const dirty = repo({ name: 'api', isClean: false })
  const text = statusLineText(model({ repos: [ahead, dirty] }))
  expect(text).toContain('2 repos')
  expect(text).toContain('1 dirty')
  expect(text).toContain('oc ↑19')
})

test('statusLineText notes multi-repo worktree groups as features', async () => {
  const text = statusLineText(
    model({
      worktreeGroups: [{ branch: 'shared', members: [{ repo: 'oc', path: '/a' }, { repo: 'api', path: '/b' }] }],
    }),
  )
  expect(text).toContain('1 feature across repos')
})

test('renderWorktreeName is a list item with the branch, or marks a detached worktree', async () => {
  expect(renderWorktreeName('foo')).toBe('• foo')
  expect(renderWorktreeName(null)).toBe('• (detached)')
})

test('changeSegments gives the counts of what changed, is new and is deleted, with separators between', async () => {
  expect(changeSegments({ changed: 3, added: 2, deleted: 1 })).toEqual([
    { text: '3 changed', tone: 'changed' },
    { text: '·', tone: 'separator' },
    { text: '2 new', tone: 'new' },
    { text: '·', tone: 'separator' },
    { text: '1 deleted', tone: 'deleted' },
  ])
})

test('changeSegments leaves out the kinds with a count of zero, and their separators', async () => {
  expect(changeSegments({ changed: 0, added: 2, deleted: 0 })).toEqual([{ text: '2 new', tone: 'new' }])
  expect(changeSegments({ changed: 1, added: 0, deleted: 4 })).toEqual([
    { text: '1 changed', tone: 'changed' },
    { text: '·', tone: 'separator' },
    { text: '4 deleted', tone: 'deleted' },
  ])
})

test('changeSegments is empty for a clean worktree or one that could not be checked', async () => {
  expect(changeSegments({ changed: 0, added: 0, deleted: 0 })).toEqual([])
  expect(changeSegments(undefined)).toEqual([])
})

test('prsNote says nothing once PRs are in, and otherwise why there are none to show', async () => {
  expect(prsNote(undefined)).toBe('PRs not loaded yet.')
  expect(prsNote({ status: 'loading' })).toBe('Loading PRs…')
  expect(prsNote({ status: 'error', message: 'gh: not logged in' })).toBe('gh: not logged in')
  expect(prsNote({ status: 'ok', prs: [], fetchedAt: 0 })).toBeUndefined()
})

const NOW = Date.parse('2026-10-09T19:52:19Z')

test('formatAge counts minutes, hours and days back from now', async () => {
  expect(formatAge('2026-10-09T19:52:00Z', NOW)).toBe('just now')
  expect(formatAge('2026-10-09T19:47:19Z', NOW)).toBe('5m ago')
  expect(formatAge('2026-10-09T16:52:19Z', NOW)).toBe('3h ago')
  expect(formatAge('2026-10-07T19:52:19Z', NOW)).toBe('2d ago')
})

test('formatAge treats a time in the future as just now and an unreadable one as unknown', async () => {
  expect(formatAge('2026-10-10T19:52:19Z', NOW)).toBe('just now')
  expect(formatAge('not a date', NOW)).toBe('unknown')
})

test('unresolvedLabel gives the count, a plus when it is only a lower bound, none for zero, and unknown without one', async () => {
  expect(unresolvedLabel({ count: 3, isLowerBound: false })).toBe('3')
  expect(unresolvedLabel({ count: 100, isLowerBound: true })).toBe('100+')
  expect(unresolvedLabel({ count: 0, isLowerBound: false })).toBe('none')
  expect(unresolvedLabel(null)).toBe('unknown')
})

test('unresolvedLabel says unknown for details saved before the count existed', async () => {
  expect(unresolvedLabel(undefined)).toBe('unknown')
})

test('checksLabel lists only the counts that are not zero', async () => {
  expect(checksLabel({ passed: 30, failed: 0, pending: 1 })).toBe('30 passed · 1 pending')
  expect(checksLabel({ passed: 2, failed: 3, pending: 0 })).toBe('2 passed · 3 failed')
  expect(checksLabel({ passed: 0, failed: 0, pending: 0 })).toBe('none')
})

const aDetails = (overrides: Partial<PrDetails> = {}): PrDetails => ({
  number: 3029,
  title: 'Add the thing',
  state: 'OPEN',
  isDraft: false,
  author: 'jeff-roche',
  branch: 'topology',
  baseBranch: 'master',
  url: 'https://github.com/org/api/pull/3029',
  updatedAt: '2026-10-07T19:52:19Z',
  unresolved: { count: 1, isLowerBound: false },
  checks: { passed: 30, failed: 0, pending: 1 },
  additions: 4582,
  deletions: 78,
  changedFiles: 23,
  ...overrides,
})

test('renderPrDetailsMarkdown makes the title a header and the PR a link, with the details in between', async () => {
  expect(renderPrDetailsMarkdown(aDetails(), NOW)).toBe(
    [
      '## Add the thing',
      '',
      '`topology` → `master` · by jeff-roche · updated 2d ago',
      '',
      '- **Unresolved review comments:** 1',
      '- **Checks:** 30 passed · 1 pending',
      '- **Changes:** +4582 −78 · 23 files',
      '',
      '[Open on GitHub](https://github.com/org/api/pull/3029)',
    ].join('\n'),
  )
})

test('renderPrDetailsMarkdown says one file in the singular, and leaves the status to the PR box header', async () => {
  const md = renderPrDetailsMarkdown(aDetails({ isDraft: true, changedFiles: 1 }), NOW)
  expect(md).toContain('- **Changes:** +4582 −78 · 1 file')
  expect(md).not.toContain('OPEN')
  expect(md).not.toContain('draft')
})

test('prStatusLabel names the state, calling an open draft a draft', async () => {
  expect(prStatusLabel(aDetails())).toBe('Open')
  expect(prStatusLabel(aDetails({ isDraft: true }))).toBe('Draft')
  expect(prStatusLabel(aDetails({ state: 'MERGED' }))).toBe('Merged')
  expect(prStatusLabel(aDetails({ state: 'CLOSED' }))).toBe('Closed')
  expect(prStatusLabel(aDetails({ state: 'WEIRD' }))).toBe('Weird')
})

test('prStatusLabel does not call a merged or closed PR a draft', async () => {
  expect(prStatusLabel(aDetails({ state: 'MERGED', isDraft: true }))).toBe('Merged')
  expect(prStatusLabel(aDetails({ state: 'CLOSED', isDraft: true }))).toBe('Closed')
})

test('prStatusColor is a theme color for each state, and plain text for one it does not know', async () => {
  expect(prStatusColor(aDetails())).toBe('success')
  expect(prStatusColor(aDetails({ isDraft: true }))).toBe('inactive')
  expect(prStatusColor(aDetails({ state: 'MERGED' }))).toBe('merged')
  expect(prStatusColor(aDetails({ state: 'CLOSED' }))).toBe('error')
  expect(prStatusColor(aDetails({ state: 'WEIRD' }))).toBe('text')
})

test('renderPrDetailsMarkdown escapes what markdown would read as formatting in a title, and keeps it on one line', async () => {
  const md = renderPrDetailsMarkdown(aDetails({ title: 'Fix *the* [thing]_now_ `x` <b>\nnext' }), NOW)
  expect(md.split('\n')[0]).toBe('## Fix \\*the\\* \\[thing\\]\\_now\\_ \\`x\\` \\<b\\> next')
})

test('renderPrDetailsMarkdown keeps a link and a branch from breaking out of their markdown', async () => {
  const md = renderPrDetailsMarkdown(aDetails({ url: 'https://github.com/o/r/pull/1)x y', branch: 'a`b' }), NOW)
  expect(md).toContain('[Open on GitHub](https://github.com/o/r/pull/1%29x%20y)')
  expect(md).toContain("`a'b` → `master`")
})

test('resolveSelectedRepo returns the named repo, else the first, else null', async () => {
  const repos = [repo({ name: 'api' }), repo({ name: 'oc' })]
  expect(resolveSelectedRepo(repos, 'oc')?.name).toBe('oc')
  expect(resolveSelectedRepo(repos, 'gone')?.name).toBe('api')
  expect(resolveSelectedRepo(repos, null)?.name).toBe('api')
  expect(resolveSelectedRepo([], 'x')).toBeNull()
})

test('trackingText names the upstream with how far ahead and behind it is', async () => {
  expect(trackingText({ upstream: 'origin/topology', ahead: 2, behind: 1 })).toBe('origin/topology · ↑2 ↓1')
  expect(trackingText({ upstream: 'origin/topology', ahead: 2, behind: 0 })).toBe('origin/topology · ↑2')
  expect(trackingText({ upstream: 'origin/topology', ahead: 0, behind: 3 })).toBe('origin/topology · ↓3')
})

test('trackingText says up to date when level with the upstream, and that there is none when there is none', async () => {
  expect(trackingText({ upstream: 'origin/main', ahead: 0, behind: 0 })).toBe('origin/main · up to date')
  expect(trackingText({ upstream: null, ahead: 0, behind: 0 })).toBe('no upstream')
})

test('lastCommitText gives the short hash, author and how long ago, leaving the message to its own lines', async () => {
  expect(
    lastCommitText({ hash: 'abc1234', message: 'Fix the thing', author: 'Jeff Roche', date: '2026-10-07T19:52:19Z' }, NOW),
  ).toBe('abc1234 · Jeff Roche, 2d ago')
})

test('lastCommitText says none yet for a branch with no commits', async () => {
  expect(lastCommitText(null, NOW)).toBe('none yet')
})

test('commitMessageText is the message as written, trimmed', async () => {
  expect(commitMessageText('  Fix the thing\n\nWith a body.\n\n')).toBe('Fix the thing\n\nWith a body.')
})

test('commitMessageText cuts a long message to twelve lines and says so', async () => {
  const long = Array.from({ length: 20 }, (_, i) => `line ${i + 1}`).join('\n')
  const lines = commitMessageText(long).split('\n')
  expect(lines).toHaveLength(13)
  expect(lines[11]).toBe('line 12')
  expect(lines[12]).toBe('…')
})

test('commitMessageText copes with a commit saved before messages were kept, which has none', async () => {
  expect(commitMessageText(undefined)).toBe('(no message)')
  expect(commitMessageText('   ')).toBe('(no message)')
})

test('statusSegments leads with the PR number and its status, then what changed', async () => {
  expect(statusSegments({ number: 3029, isDraft: false }, { changed: 1, added: 2, deleted: 0 })).toEqual([
    { text: '#3029', tone: 'pr' },
    { text: 'Open', tone: 'open' },
    { text: '·', tone: 'separator' },
    { text: '1 changed', tone: 'changed' },
    { text: '·', tone: 'separator' },
    { text: '2 new', tone: 'new' },
  ])
})

test('statusSegments calls a draft PR a draft', async () => {
  expect(statusSegments({ number: 7, isDraft: true }, undefined)).toEqual([
    { text: '#7', tone: 'pr' },
    { text: 'Draft', tone: 'draft' },
  ])
})

test('statusSegments is just the changes without a PR, just the PR when clean, and empty with neither', async () => {
  expect(statusSegments(undefined, { changed: 3, added: 0, deleted: 0 })).toEqual([{ text: '3 changed', tone: 'changed' }])
  expect(statusSegments({ number: 5, isDraft: false }, { changed: 0, added: 0, deleted: 0 })).toEqual([
    { text: '#5', tone: 'pr' },
    { text: 'Open', tone: 'open' },
  ])
  expect(statusSegments(undefined, undefined)).toEqual([])
})

test('repoToggleText names the repo shown, with an arrow that says whether the list is open', async () => {
  expect(repoToggleText('api', false)).toBe('api ▾')
  expect(repoToggleText('cluster-config-operator', true)).toBe('cluster-config-operator ▴')
})

test('repoOptionLabel marks the repo shown with a filled dot and the others with an open one', async () => {
  expect(repoOptionLabel('api', true)).toBe('● api')
  expect(repoOptionLabel('oc', false)).toBe('○ oc')
})

test('single-repo mode: tab label, status line and toasts speak of one repo and never of the doc', async () => {
  const single = model({ mode: 'single-repo', repos: [repo({ name: 'api', isClean: false, dirtyCount: 2, ahead: 1 })] })
  expect(tabLabel('repos', single)).toBe('Repo')
  expect(statusLineText(single)).toBe('api · main · 2 dirty · ↑1')
  expect(refreshToast(single, 'AGENTS.md', { isDocSync: false, wroteDoc: false })).toBe('Rescanned the repo and its worktrees.')
  expect(refreshToast(single, 'AGENTS.md', { isDocSync: true, wroteDoc: false })).toContain('single repo')
})

test('tabLabel names the four tabs, counting repos and active projects', async () => {
  const m = model({ managed: true, projects: [{ name: 'a', project: 'a', type: '', status: 'active', lastActive: '', jira: '', branch: '', domain: '', finished: false, tasks: { checked: 0, total: 0 } }, { name: 'b', project: 'b', type: '', status: 'done', lastActive: '', jira: '', branch: '', domain: '', finished: true, tasks: { checked: 0, total: 0 } }] })
  expect(tabLabel('repos', m)).toBe('Repos (1)')
  expect(tabLabel('projects', m)).toBe('Projects (1)')
  expect(tabLabel('projects', model())).toBe('Projects')
  expect(tabLabel('actions', m)).toBe('Actions')
  expect(tabLabel('settings', m)).toBe('Settings')
})

test('renderWorktreeName marks the current worktree like the repo picker, and keeps a bullet when there is no current one', async () => {
  expect(renderWorktreeName('foo', true)).toBe('● foo')
  expect(renderWorktreeName('bar', false)).toBe('○ bar')
  expect(renderWorktreeName(null, false)).toBe('○ (detached)')
  expect(renderWorktreeName('foo')).toBe('• foo')
})
