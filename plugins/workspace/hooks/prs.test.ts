import { expect, test } from 'claude-code/testing'

import {
  fetchPrDetails,
  fetchPrs,
  isPrsStale,
  parseGithubSlug,
  parsePrDetails,
  parsePrList,
  parseUnresolvedThreads,
  prDetailsKey,
  prForBranch,
  summarizeChecks,
} from './prs'
import type { Exec } from './prs'
import type { Pr, PrDetailsEntry, PrsEntry, RepoInfo } from './model'

const pr = (overrides: Partial<Pr> = {}): Pr => ({
  number: 3029,
  title: 'Add topologyTransitionStatus',
  branch: 'topology-transitions-status',
  isDraft: false,
  url: 'https://github.com/openshift/api/pull/3029',
  ...overrides,
})

function repo(overrides: Partial<RepoInfo> = {}): Pick<RepoInfo, 'path' | 'remotes'> {
  return {
    path: '/ws/api',
    remotes: [
      { name: 'origin', url: 'https://github.com/jeff-roche/api.git', role: 'primary' },
      { name: 'upstream', url: 'https://github.com/openshift/api.git', role: 'canonical' },
    ],
    ...overrides,
  }
}

test('parseGithubSlug reads https, scp-style and ssh remotes, with or without .git', async () => {
  expect(parseGithubSlug('https://github.com/openshift/api.git')).toBe('openshift/api')
  expect(parseGithubSlug('https://github.com/openshift/api')).toBe('openshift/api')
  expect(parseGithubSlug('git@github.com:openshift/oc.git')).toBe('openshift/oc')
  expect(parseGithubSlug('ssh://git@github.com/openshift/origin.git')).toBe('openshift/origin')
})

test('parseGithubSlug is null for a remote that is not on github.com', async () => {
  expect(parseGithubSlug('https://gitlab.com/group/project.git')).toBeNull()
  expect(parseGithubSlug('/local/path/repo.git')).toBeNull()
})

test('parsePrList maps gh json to PRs', async () => {
  const json = JSON.stringify([
    { number: 7, title: 'Fix', headRefName: 'fix-7', isDraft: true, url: 'https://github.com/o/r/pull/7' },
  ])
  expect(parsePrList(json)).toEqual([
    { number: 7, title: 'Fix', branch: 'fix-7', isDraft: true, url: 'https://github.com/o/r/pull/7' },
  ])
})

test('parsePrList accepts an empty list and rejects output that is not a PR list', async () => {
  expect(parsePrList('[]')).toEqual([])
  expect(parsePrList('not json')).toBeNull()
  expect(parsePrList('{"a":1}')).toBeNull()
  expect(parsePrList('[{"number":"x"}]')).toBeNull()
})

test('prForBranch finds the PR whose head branch matches, and nothing for a null or unmatched branch', async () => {
  const prs = [pr(), pr({ number: 1, branch: 'other' })]
  expect(prForBranch(prs, 'other')?.number).toBe(1)
  expect(prForBranch(prs, 'nope')).toBeUndefined()
  expect(prForBranch(prs, null)).toBeUndefined()
})

test('isPrsStale: missing and failed entries reload, loading does not, ok entries age out after five minutes', async () => {
  const now = 1_000_000
  const ok = (fetchedAt: number): PrsEntry => ({ status: 'ok', prs: [], fetchedAt })
  expect(isPrsStale(undefined, now)).toBe(true)
  const failed: PrsEntry = { status: 'error', message: 'x' }
  expect(isPrsStale(failed, now)).toBe(true)
  expect(isPrsStale({ status: 'loading' }, now)).toBe(false)
  expect(isPrsStale(ok(now - 60_000), now)).toBe(false)
  expect(isPrsStale(ok(now - 6 * 60_000), now)).toBe(true)
})

test('fetchPrs asks gh for your open PRs on the PR-target repo', async () => {
  const calls: { argv: string[]; cwd: string }[] = []
  const exec: Exec = async (argv, cwd) => {
    calls.push({ argv, cwd })
    return { exitCode: 0, stdout: JSON.stringify([{ number: 3029, title: 'T', headRefName: 'b', isDraft: false, url: 'u' }]), stderr: '' }
  }
  const entry = await fetchPrs(exec, repo(), 42)
  expect(entry).toEqual({ status: 'ok', prs: [{ number: 3029, title: 'T', branch: 'b', isDraft: false, url: 'u' }], fetchedAt: 42 })
  expect(calls[0]?.cwd).toBe('/ws/api')
  expect(calls[0]?.argv.slice(0, 7)).toEqual(['gh', 'pr', 'list', '--repo', 'openshift/api', '--author', '@me'])
})

test('fetchPrs reports a repo with no GitHub remote without calling gh', async () => {
  let called = false
  const exec: Exec = async () => {
    called = true
    return { exitCode: 0, stdout: '[]', stderr: '' }
  }
  const entry = await fetchPrs(exec, repo({ remotes: [] }), 1)
  expect(entry).toEqual({ status: 'error', message: 'No GitHub remote to look up PRs on.' })
  expect(called).toBe(false)
})

test('fetchPrs surfaces gh failing, with its own first line of stderr', async () => {
  const exec: Exec = async () => ({ exitCode: 1, stdout: '', stderr: 'gh: To get started with GitHub CLI, please run: gh auth login\nmore' })
  expect(await fetchPrs(exec, repo(), 1)).toEqual({
    status: 'error',
    message: 'gh: To get started with GitHub CLI, please run: gh auth login',
  })
})

test('fetchPrs surfaces gh not starting at all', async () => {
  const exec: Exec = async () => {
    throw new Error('spawn gh ENOENT')
  }
  expect(await fetchPrs(exec, repo(), 1)).toEqual({ status: 'error', message: "Couldn't run gh: spawn gh ENOENT" })
})

test('fetchPrs surfaces output it cannot read', async () => {
  const exec: Exec = async () => ({ exitCode: 0, stdout: 'garbage', stderr: '' })
  expect(await fetchPrs(exec, repo(), 1)).toEqual({ status: 'error', message: 'Unexpected output from gh.' })
})

test('isPrsStale applies the same ageing to the details of one PR', async () => {
  const now = 1_000_000
  const ok = (fetchedAt: number): PrDetailsEntry => ({
    status: 'ok',
    fetchedAt,
    details: {
      number: 1, title: 't', state: 'OPEN', isDraft: false, author: 'a', branch: 'b', baseBranch: 'm', url: 'u',
      updatedAt: '2026-10-07T19:52:19Z', unresolved: null, checks: { passed: 0, failed: 0, pending: 0 },
      additions: 0, deletions: 0, changedFiles: 0,
    },
  })
  expect(isPrsStale(ok(now - 60_000), now)).toBe(false)
  expect(isPrsStale(ok(now - 6 * 60_000), now)).toBe(true)
  expect(isPrsStale({ status: 'loading' }, now)).toBe(false)
})

test('prDetailsKey names a PR by its repo and number', async () => {
  expect(prDetailsKey('api', 3029)).toBe('api#3029')
})

test('summarizeChecks counts commit statuses and check runs into passed, failed and pending', async () => {
  const rollup = [
    { state: 'SUCCESS' }, { state: 'FAILURE' }, { state: 'ERROR' }, { state: 'PENDING' }, { state: 'EXPECTED' },
    { status: 'COMPLETED', conclusion: 'SUCCESS' }, { conclusion: 'NEUTRAL' }, { conclusion: 'SKIPPED' },
    { conclusion: 'TIMED_OUT' }, { conclusion: 'CANCELLED' },
    { status: 'IN_PROGRESS', conclusion: '' }, { status: 'QUEUED', conclusion: null },
  ]
  expect(summarizeChecks(rollup)).toEqual({ passed: 4, failed: 4, pending: 4 })
})

test('summarizeChecks is all zeros for no checks or something that is not a list', async () => {
  expect(summarizeChecks([])).toEqual({ passed: 0, failed: 0, pending: 0 })
  expect(summarizeChecks(undefined)).toEqual({ passed: 0, failed: 0, pending: 0 })
  expect(summarizeChecks({ not: 'a list' })).toEqual({ passed: 0, failed: 0, pending: 0 })
})

const DETAILS_JSON = JSON.stringify({
  number: 3029,
  title: 'T',
  state: 'OPEN',
  isDraft: false,
  author: { login: 'me' },
  headRefName: 'b',
  baseRefName: 'master',
  url: 'https://github.com/org/api/pull/3029',
  updatedAt: '2026-10-07T19:52:19Z',
  statusCheckRollup: [{ __typename: 'StatusContext', state: 'SUCCESS' }, { __typename: 'StatusContext', state: 'PENDING' }],
  additions: 5,
  deletions: 2,
  changedFiles: 3,
})

test('parsePrDetails maps gh pr view json, leaving the unresolved count for the second lookup', async () => {
  expect(parsePrDetails(DETAILS_JSON)).toEqual({
    number: 3029,
    title: 'T',
    state: 'OPEN',
    isDraft: false,
    author: 'me',
    branch: 'b',
    baseBranch: 'master',
    url: 'https://github.com/org/api/pull/3029',
    updatedAt: '2026-10-07T19:52:19Z',
    checks: { passed: 1, failed: 0, pending: 1 },
    unresolved: null,
    additions: 5,
    deletions: 2,
    changedFiles: 3,
  })
})

test('parsePrDetails rejects output that is not a PR', async () => {
  expect(parsePrDetails('garbage')).toBeNull()
  expect(parsePrDetails('[]')).toBeNull()
  expect(parsePrDetails(JSON.stringify({ ...JSON.parse(DETAILS_JSON), title: undefined }))).toBeNull()
})

test('fetchPrDetails asks gh to view that PR on the PR-target repo', async () => {
  const calls: { argv: string[]; cwd: string }[] = []
  const exec: Exec = async (argv, cwd) => {
    calls.push({ argv, cwd })
    return { exitCode: 0, stdout: DETAILS_JSON, stderr: '' }
  }
  const entry = await fetchPrDetails(exec, repo(), 3029, 42)
  expect(entry.status).toBe('ok')
  expect(entry.status === 'ok' && entry.fetchedAt).toBe(42)
  expect(calls[0]?.cwd).toBe('/ws/api')
  expect(calls[0]?.argv.slice(0, 6)).toEqual(['gh', 'pr', 'view', '3029', '--repo', 'openshift/api'])
})

test('fetchPrDetails reports the same failures fetchPrs does', async () => {
  expect(await fetchPrDetails(async () => ({ exitCode: 0, stdout: '', stderr: '' }), repo({ remotes: [] }), 1, 1)).toEqual({
    status: 'error',
    message: 'No GitHub remote to look up PRs on.',
  })
  expect(
    await fetchPrDetails(async () => ({ exitCode: 1, stdout: '', stderr: 'no such PR\nmore' }), repo(), 1, 1),
  ).toEqual({ status: 'error', message: 'no such PR' })
  expect(
    await fetchPrDetails(async () => {
      throw new Error('spawn gh ENOENT')
    }, repo(), 1, 1),
  ).toEqual({ status: 'error', message: "Couldn't run gh: spawn gh ENOENT" })
  expect(await fetchPrDetails(async () => ({ exitCode: 0, stdout: 'garbage', stderr: '' }), repo(), 1, 1)).toEqual({
    status: 'error',
    message: 'Unexpected output from gh.',
  })
})

const threadsJson = (resolved: boolean[], hasNextPage = false) =>
  JSON.stringify({
    data: {
      repository: {
        pullRequest: {
          reviewThreads: {
            totalCount: resolved.length,
            pageInfo: { hasNextPage },
            nodes: resolved.map(isResolved => ({ isResolved, isOutdated: false, comments: { totalCount: 1 } })),
          },
        },
      },
    },
  })

test('parseUnresolvedThreads counts the threads that are not resolved', async () => {
  expect(parseUnresolvedThreads(threadsJson([true, true, false, true, false]))).toEqual({ count: 2, isLowerBound: false })
  expect(parseUnresolvedThreads(threadsJson([true, true]))).toEqual({ count: 0, isLowerBound: false })
  expect(parseUnresolvedThreads(threadsJson([]))).toEqual({ count: 0, isLowerBound: false })
})

test('parseUnresolvedThreads says the count is a lower bound when there are more threads than one page', async () => {
  expect(parseUnresolvedThreads(threadsJson([false, true], true))).toEqual({ count: 1, isLowerBound: true })
})

test('parseUnresolvedThreads is null for output that has no review threads in it', async () => {
  expect(parseUnresolvedThreads('garbage')).toBeNull()
  expect(parseUnresolvedThreads('{"data":{"repository":{"pullRequest":null}}}')).toBeNull()
  expect(parseUnresolvedThreads('[]')).toBeNull()
})

/** gh answering `pr view` with the details and `api graphql` with the threads, each as given. */
const ghAnswering = (view: { exitCode: number; stdout: string }, threads: { exitCode: number; stdout: string }) => {
  const calls: string[][] = []
  const exec: Exec = async argv => {
    calls.push(argv)
    const answer = argv[1] === 'api' ? threads : view
    return { ...answer, stderr: answer.exitCode === 0 ? '' : 'boom' }
  }
  return { exec, calls }
}

test('fetchPrDetails adds the unresolved review thread count from a second, GraphQL lookup', async () => {
  const { exec, calls } = ghAnswering(
    { exitCode: 0, stdout: DETAILS_JSON },
    { exitCode: 0, stdout: threadsJson([true, false, true]) },
  )
  const entry = await fetchPrDetails(exec, repo(), 3029, 7)
  expect(entry.status === 'ok' && entry.details.unresolved).toEqual({ count: 1, isLowerBound: false })

  const graphql = calls.find(argv => argv[1] === 'api')
  expect(graphql?.slice(0, 3)).toEqual(['gh', 'api', 'graphql'])
  expect(graphql).toContain('owner=openshift')
  expect(graphql).toContain('name=api')
  expect(graphql).toContain('number=3029')
})

test('fetchPrDetails still shows the details, with the count unknown, when the thread lookup fails', async () => {
  const { exec } = ghAnswering({ exitCode: 0, stdout: DETAILS_JSON }, { exitCode: 1, stdout: '' })
  const entry = await fetchPrDetails(exec, repo(), 3029, 7)
  expect(entry.status).toBe('ok')
  expect(entry.status === 'ok' && entry.details.unresolved).toBeNull()
  expect(entry.status === 'ok' && entry.details.title).toBe('T')
})

test('fetchPrDetails does not look up threads for a PR it could not view', async () => {
  const { exec, calls } = ghAnswering({ exitCode: 1, stdout: '' }, { exitCode: 0, stdout: threadsJson([false]) })
  expect((await fetchPrDetails(exec, repo(), 3029, 7)).status).toBe('error')
  expect(calls.some(argv => argv[1] === 'api')).toBe(false)
})
