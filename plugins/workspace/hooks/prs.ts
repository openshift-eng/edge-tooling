// Pull requests for a repo, looked up with the `gh` CLI: your open PRs, and one PR's details.
// Everything that runs a command takes it as an `Exec` argument, so this module never touches
// `$` and its behaviour is covered by plain unit tests.

import { prTarget } from './discovery'
import type { ExecResult } from './discovery'
import type { Pr, PrDetails, PrDetailsEntry, PrsEntry, RepoInfo } from './model'

export type Exec = (argv: string[], cwd: string) => Promise<ExecResult>

/** How long a fetched list, or one PR's details, is trusted before it is fetched again. */
const PRS_TTL_MS = 5 * 60 * 1000

/** `owner/name` for a github.com remote URL (https, scp-style or ssh), or null for any other host. */
export function parseGithubSlug(url: string): string | null {
  const match = /github\.com[:/]([^/\s]+)\/([^/\s]+?)(?:\.git)?\/?$/.exec(url)
  const owner = match?.[1]
  const name = match?.[2]
  return owner !== undefined && name !== undefined ? `${owner}/${name}` : null
}

/** The PRs in `gh pr list --json` output, or null when it isn't a list of PRs. */
export function parsePrList(stdout: string): Pr[] | null {
  let data: unknown
  try {
    data = JSON.parse(stdout)
  } catch {
    return null
  }
  if (!Array.isArray(data)) return null

  const prs: Pr[] = []
  for (const item of data) {
    if (typeof item !== 'object' || item === null) return null
    const o = item as Record<string, unknown>
    if (
      typeof o.number !== 'number' ||
      typeof o.title !== 'string' ||
      typeof o.headRefName !== 'string' ||
      typeof o.url !== 'string'
    ) {
      return null
    }
    prs.push({ number: o.number, title: o.title, branch: o.headRefName, isDraft: o.isDraft === true, url: o.url })
  }
  return prs
}

const PASSING = new Set(['SUCCESS', 'NEUTRAL', 'SKIPPED'])
const FAILING = new Set(['FAILURE', 'ERROR', 'TIMED_OUT', 'CANCELLED', 'ACTION_REQUIRED', 'STARTUP_FAILURE'])

/**
 * Passed, failed and pending counts for `gh`'s `statusCheckRollup`, which mixes check runs (a
 * `conclusion` once complete) with commit statuses (a `state`). Anything not yet decided is pending.
 */
export function summarizeChecks(rollup: unknown): PrDetails['checks'] {
  const checks = { passed: 0, failed: 0, pending: 0 }
  if (!Array.isArray(rollup)) return checks

  for (const item of rollup) {
    const o = (typeof item === 'object' && item !== null ? item : {}) as Record<string, unknown>
    const outcome = [o.conclusion, o.state].find((v): v is string => typeof v === 'string' && v !== '')
    if (outcome !== undefined && PASSING.has(outcome)) checks.passed += 1
    else if (outcome !== undefined && FAILING.has(outcome)) checks.failed += 1
    else checks.pending += 1
  }
  return checks
}

/** The PR in `gh pr view --json` output, or null when it isn't one. */
export function parsePrDetails(stdout: string): PrDetails | null {
  let data: unknown
  try {
    data = JSON.parse(stdout)
  } catch {
    return null
  }
  if (typeof data !== 'object' || data === null || Array.isArray(data)) return null

  const o = data as Record<string, unknown>
  if (
    typeof o.number !== 'number' ||
    typeof o.title !== 'string' ||
    typeof o.state !== 'string' ||
    typeof o.headRefName !== 'string' ||
    typeof o.baseRefName !== 'string' ||
    typeof o.url !== 'string' ||
    typeof o.updatedAt !== 'string'
  ) {
    return null
  }

  const author = o.author as Record<string, unknown> | null | undefined
  const count = (v: unknown) => (typeof v === 'number' ? v : 0)
  return {
    number: o.number,
    title: o.title,
    state: o.state,
    isDraft: o.isDraft === true,
    author: typeof author?.login === 'string' ? author.login : 'unknown',
    branch: o.headRefName,
    baseBranch: o.baseRefName,
    url: o.url,
    updatedAt: o.updatedAt,
    checks: summarizeChecks(o.statusCheckRollup),
    unresolved: null,
    additions: count(o.additions),
    deletions: count(o.deletions),
    changedFiles: count(o.changedFiles),
  }
}

const REVIEW_THREADS_QUERY =
  'query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100){pageInfo{hasNextPage}nodes{isResolved}}}}}'

/** Follows `path` through nested objects, or undefined as soon as something along it is not one. */
function dig(value: unknown, ...path: string[]): unknown {
  let current = value
  for (const key of path) {
    if (typeof current !== 'object' || current === null) return undefined
    current = (current as Record<string, unknown>)[key]
  }
  return current
}

/** The unresolved review threads in the GraphQL answer, or null when it has no review threads in it. */
export function parseUnresolvedThreads(stdout: string): NonNullable<PrDetails['unresolved']> | null {
  let data: unknown
  try {
    data = JSON.parse(stdout)
  } catch {
    return null
  }
  const threads = dig(data, 'data', 'repository', 'pullRequest', 'reviewThreads')
  const nodes = dig(threads, 'nodes')
  if (!Array.isArray(nodes)) return null
  return {
    count: nodes.filter(node => dig(node, 'isResolved') === false).length,
    isLowerBound: dig(threads, 'pageInfo', 'hasNextPage') === true,
  }
}

/** The PR whose head branch is `branch`, if any. */
export function prForBranch(prs: readonly Pr[], branch: string | null): Pr | undefined {
  return branch === null ? undefined : prs.find(p => p.branch === branch)
}

/** Names one PR's details in state: its repo and number. */
export function prDetailsKey(repoName: string, number: number): string {
  return `${repoName}#${number}`
}

/** The part of a fetched entry that says whether it is due for another fetch. */
type Fetched = { status: 'loading' } | { status: 'error' } | { status: 'ok'; fetchedAt: number }

/**
 * Whether something fetched should be fetched (again): a repo's PRs, one PR's details, or a worktree's details.
 * Never fetched, failed last time, or aged out.
 */
export function isPrsStale(entry: Fetched | undefined, now: number): boolean {
  if (entry === undefined || entry.status === 'error') return true
  if (entry.status === 'loading') return false
  return now - entry.fetchedAt > PRS_TTL_MS
}

type GhResult<T> = { ok: true; value: T } | { ok: false; message: string }

/**
 * Runs `gh` against the GitHub repo the repo's PR-target remote points at, so PRs raised from a fork are
 * found under the upstream repo. Never throws: failures come back as a message the pane shows.
 */
async function runGh<T>(
  exec: Exec,
  repo: Pick<RepoInfo, 'path' | 'remotes'>,
  args: (slug: string) => string[],
  parse: (stdout: string) => T | null,
): Promise<GhResult<T>> {
  const target = prTarget(repo.remotes)
  const slug = target ? parseGithubSlug(target.url) : null
  if (slug === null) return { ok: false, message: 'No GitHub remote to look up PRs on.' }

  let result: ExecResult
  try {
    result = await exec(args(slug), repo.path)
  } catch (err) {
    return { ok: false, message: `Couldn't run gh: ${err instanceof Error ? err.message : String(err)}` }
  }

  if (result.exitCode !== 0) {
    const firstLine = result.stderr.trim().split('\n')[0] ?? ''
    return { ok: false, message: firstLine || `gh exited ${result.exitCode}` }
  }

  const value = parse(result.stdout)
  return value === null ? { ok: false, message: 'Unexpected output from gh.' } : { ok: true, value }
}

/** Your open PRs on the PR-target repo. */
export async function fetchPrs(exec: Exec, repo: Pick<RepoInfo, 'path' | 'remotes'>, now: number): Promise<PrsEntry> {
  const result = await runGh(
    exec,
    repo,
    slug => [
      'gh', 'pr', 'list', '--repo', slug, '--author', '@me', '--state', 'open', '--limit', '30',
      '--json', 'number,title,headRefName,isDraft,url',
    ],
    parsePrList,
  )
  return result.ok ? { status: 'ok', prs: result.value, fetchedAt: now } : { status: 'error', message: result.message }
}

/** One PR's details on the PR-target repo. */
export async function fetchPrDetails(
  exec: Exec,
  repo: Pick<RepoInfo, 'path' | 'remotes'>,
  number: number,
  now: number,
): Promise<PrDetailsEntry> {
  const view = await runGh(
    exec,
    repo,
    slug => [
      'gh', 'pr', 'view', String(number), '--repo', slug, '--json',
      'number,title,state,isDraft,author,headRefName,baseRefName,url,updatedAt,statusCheckRollup,additions,deletions,changedFiles',
    ],
    parsePrDetails,
  )
  if (!view.ok) return { status: 'error', message: view.message }

  // Review threads are only in the GraphQL API, so the unresolved count is a second call. If it fails the
  // details still show, with the count unknown.
  const threads = await runGh(
    exec,
    repo,
    slug => {
      const [owner = '', name = ''] = slug.split('/')
      return ['gh', 'api', 'graphql', '-f', `query=${REVIEW_THREADS_QUERY}`, '-F', `owner=${owner}`, '-F', `name=${name}`, '-F', `number=${number}`]
    },
    parseUnresolvedThreads,
  )
  return {
    status: 'ok',
    details: { ...view.value, unresolved: threads.ok ? threads.value : null },
    fetchedAt: now,
  }
}
