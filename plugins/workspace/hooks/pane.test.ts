import { expect, test } from 'claude-code/testing'

import type { On } from 'claude-code'

import type { PrDetails, RepoInfo, WorkspaceModel, WorktreeDetails } from './model'

// The pane drawn by the real hooks on each surface: `drawn()` rejects when the surface's element
// table refuses the tree, which is the check the pure-function tests can't make.

const repo = (name: string, overrides: Partial<RepoInfo> = {}): RepoInfo => ({
  name,
  path: `/ws/${name}`,
  branch: 'master',
  ahead: 0,
  behind: 0,
  dirtyCount: 0,
  isClean: true,
  defaultBranch: 'master',
  remotes: [
    { name: 'origin', url: `https://github.com/me/${name}.git`, role: 'primary' },
    { name: 'upstream', url: `https://github.com/org/${name}.git`, role: 'canonical' },
  ],
  worktrees: [{ path: `/ws/${name}`, branch: 'master' }],
  role: null,
  ...overrides,
})

const TOPOLOGY = '/ws/api/.worktrees/topology'
const SCRATCH = '/ws/api/.worktrees/scratch'

const MODEL_VALUE: WorkspaceModel = {
  root: '/ws',
  mode: 'workspace',
  managed: false,
  projects: [],
  projectsError: null,
  repos: [
    repo('api', {
      dirtyCount: 2,
      isClean: false,
      changes: { changed: 2, added: 0, deleted: 0 },
      worktrees: [
        { path: '/ws/api', branch: 'master' },
        { path: TOPOLOGY, branch: 'topology', changes: { changed: 1, added: 2, deleted: 3 } },
        { path: SCRATCH, branch: 'scratch' },
      ],
    }),
    repo('oc'),
  ],
  otherFolders: ['docs'],
  worktreeGroups: [],
  scannedAt: 1,
}

const PANE = {
  plugin: 'workspace',
  component: 'Pane',
  requestId: 'workspace',
  props: {
    title: 'Workspace',
    isFocused: false,
    bodyColumns: 80,
    placement: 'inline',
    scroll: { offset: 0, bodyRows: 24 },
    view: {},
  },
} as const

// `topology` has an open PR; `scratch` and the main checkout do not.
const SEEDED_PRS = {
  api: {
    status: 'ok',
    fetchedAt: 1,
    prs: [{ number: 3029, title: 'Add the thing', branch: 'topology', isDraft: false, url: 'https://github.com/org/api/pull/3029' }],
  },
} as const

const DETAILS: PrDetails = {
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
}

const WORKTREE_DETAILS: WorktreeDetails = {
  upstream: 'origin/topology',
  ahead: 2,
  behind: 1,
  changes: { changed: 1, added: 2, deleted: 3 },
  lastCommit: {
    hash: 'abc1234',
    message: 'Fix the thing\n\nWith a longer explanation.',
    author: 'Jeff Roche',
    date: '2026-10-07T19:52:19Z',
  },
}

// A test's `$` has no `$.state`; it answers the plugin's reads from beneath instead. The hook's result
// wraps the read, `{ value: { value, version } }`. Only what a test reads is seeded: a value answered at
// a fixed version would never accept the plugin's own writes.
const seed = (on: On, key: 'model' | 'prs' | 'openWorktree' | 'worktreeDetails' | 'prDetails', value: unknown) =>
  // The matcher types `key` as one declared literal, so a key chosen at run time needs the cast.
  on('state.get', { plugin: 'workspace', key } as { plugin: 'workspace'; key: 'model' }, () => ({
    value: { value, version: 1 },
  }))

const SURFACES = ['terminal', 'desktop', 'vscode', 'mobile'] as const

// `drawn()` is plain data: elements with a type, props and children, the children being more elements or strings.
type Node = { type?: string; props?: Record<string, unknown>; children?: unknown[] }
const isNode = (value: unknown): value is Node => typeof value === 'object' && value !== null
const findNode = (node: unknown, matches: (n: Node) => boolean): Node | undefined => {
  if (!isNode(node)) return undefined
  if (matches(node)) return node
  for (const child of node.children ?? []) {
    const found = findNode(child, matches)
    if (found) return found
  }
  return undefined
}
const findNodes = (node: unknown, matches: (n: Node) => boolean): Node[] =>
  !isNode(node) ? [] : [...(matches(node) ? [node] : []), ...(node.children ?? []).flatMap(child => findNodes(child, matches))]
/** Every string drawn under `node`, in document order. */
const textsUnder = (node: unknown): string[] =>
  typeof node === 'string' ? [node] : isNode(node) ? (node.children ?? []).flatMap(textsUnder) : []

test('the Repos tab lists worktrees, each name a button, with no PR button and no box', async ($, on) => {
  seed(on, 'model', MODEL_VALUE)
  seed(on, 'prs', SEEDED_PRS)

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.drawn()

    expect(await ui.find({ type: 'Text', text: /Worktrees/ })).toBeDefined()
    for (const [path, name] of [['/ws/api', '• master'], [TOPOLOGY, '• topology'], [SCRATCH, '• scratch']] as const) {
      expect((await ui.find({ type: 'Button', key: `worktree-${path}` }))?.props.label).toBe(name)
    }

    // The PR is on the row's status line now, not a button of its own.
    expect(await ui.find({ type: 'Button', key: 'open-pr-3029' })).toBeUndefined()
    expect((await ui.find({ type: 'Button', key: 'reload-prs' }))?.props.label).toBe('Refresh')
    // The heading row spreads the title and Refresh to opposite edges.
    expect((await ui.find({ type: 'Box', key: 'worktrees-header' }))?.props.justifyContent).toBe('space-between')

    // The worktree box is hidden until a worktree is opened.
    expect(await ui.find({ type: 'Button', key: 'close-worktree' })).toBeUndefined()

    // The repo picker is a button saying which repo is shown, with its list closed to start with: a dropdown
    // made of buttons, since a click presses a button and a Select takes keys only.
    expect(await ui.find({ type: 'Select' })).toBeUndefined()
    expect((await ui.find({ type: 'Button', key: 'repo-picker-toggle' }))?.props).toMatchObject({
      label: 'api ▾',
      plain: true,
    })
    // Its label is bold, beside the button: a button has no bold of its own.
    expect((await ui.find({ type: 'Text', text: /^Repo:$/ }))?.props.bold).toBe(true)
    expect(await ui.find({ type: 'Box', key: 'repo-list' })).toBeUndefined()
    expect(await ui.find({ type: 'Button', key: 'repo-oc' })).toBeUndefined()
    await ui.unmount()
  }
})

test('each row has a status line with its PR and what changed, in colour, and no path line or summary', async ($, on) => {
  seed(on, 'model', MODEL_VALUE)
  seed(on, 'prs', SEEDED_PRS)

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    const tree = await ui.drawn()
    const row = (key: string) => textsUnder(findNode(tree, n => n.props?.key === key))

    // The status line: the PR and its status, then what changed; none for a clean worktree with no PR.
    expect(row('/ws/api')).toEqual(['2 changed'])
    expect(row(TOPOLOGY)).toEqual(['#3029', 'Open', '·', '1 changed', '·', '2 new', '·', '3 deleted'])
    expect(row(SCRATCH)).toEqual([])

    // Each kind in its own theme colour: changed warning, new success, deleted error, an open PR success.
    expect((await ui.find({ type: 'Text', text: /^1 changed$/ }))?.props.color).toBe('warning')
    expect((await ui.find({ type: 'Text', text: /^2 new$/ }))?.props.color).toBe('success')
    expect((await ui.find({ type: 'Text', text: /^3 deleted$/ }))?.props.color).toBe('error')
    expect((await ui.find({ type: 'Text', text: /^Open$/ }))?.props.color).toBe('success')
    expect((await ui.find({ type: 'Text', text: /^#3029$/ }))?.props.color).toBeUndefined()
    expect((await ui.find({ type: 'Text', text: /^·$/ }))?.props.dimColor).toBe(true)

    // No path under a worktree (the box has it), and none of the one-line repo summary.
    expect(await ui.find({ type: 'Text', text: /api\/\.worktrees|main checkout/ })).toBeUndefined()
    expect(textsUnder(tree).some(text => text.includes('PRs→'))).toBe(false)
    expect(await ui.find({ type: 'Text', text: /^api: master/ })).toBeUndefined()
    await ui.unmount()
  }
})

test('a draft PR says Draft on its row, in the muted colour', async ($, on) => {
  seed(on, 'model', MODEL_VALUE)
  seed(on, 'prs', { api: { ...SEEDED_PRS.api, prs: [{ ...SEEDED_PRS.api.prs[0], isDraft: true }] } })

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.drawn()
  expect((await ui.find({ type: 'Text', text: /^Draft$/ }))?.props.color).toBe('inactive')
  await ui.unmount()
})

/** The pane at a given width, the way the engine hands it to the hook. */
const paneAt = (bodyColumns: number) => ({ ...PANE, props: { ...PANE.props, bodyColumns } })

test('at a narrow width the worktree box goes below the worktrees list, and beside it when there is room', async ($, on) => {
  seed(on, 'model', MODEL_VALUE)
  seed(on, 'prs', SEEDED_PRS)
  seed(on, 'openWorktree', { repo: 'api', path: TOPOLOGY })
  seed(on, 'worktreeDetails', { [TOPOLOGY]: { status: 'ok', fetchedAt: 1, details: WORKTREE_DETAILS } })
  seed(on, 'prDetails', { 'api#3029': { status: 'ok', fetchedAt: 1, details: DETAILS } })

  for (const surface of SURFACES) {
    for (const [bodyColumns, direction, width] of [
      [60, 'column', '100%'],
      [99, 'column', '100%'],
      [100, 'row', '50%'],
      [160, 'row', '50%'],
    ] as const) {
      const ui = await $.ui.mount({ ...paneAt(bodyColumns), surface })
      await ui.drawn()

      // Stacked, each box takes the whole width; side by side, each takes half.
      expect((await ui.find({ type: 'Box', key: 'repos-body' }))?.props.flexDirection).toBe(direction)
      expect((await ui.find({ type: 'Box', key: 'worktrees-box' }))?.props.width).toBe(width)
      expect((await ui.find({ type: 'Box', key: 'worktree-box' }))?.props.width).toBe(width)
      await ui.unmount()
    }
  }
})

test('with no worktree open, the list takes the whole width at any pane width', async ($, on) => {
  seed(on, 'model', MODEL_VALUE)
  seed(on, 'prs', SEEDED_PRS)

  for (const bodyColumns of [60, 160]) {
    const ui = await $.ui.mount({ ...paneAt(bodyColumns), surface: 'terminal' })
    await ui.drawn()
    expect((await ui.find({ type: 'Box', key: 'worktrees-box' }))?.props.width).toBe('100%')
    expect(await ui.find({ type: 'Box', key: 'worktree-box' })).toBeUndefined()
    await ui.unmount()
  }
})

test('in a narrow box a label keeps its full width, and the value after it wraps instead', async ($, on) => {
  seed(on, 'model', MODEL_VALUE)
  seed(on, 'prs', SEEDED_PRS)
  seed(on, 'openWorktree', { repo: 'api', path: TOPOLOGY })
  seed(on, 'worktreeDetails', { [TOPOLOGY]: { status: 'ok', fetchedAt: 1, details: WORKTREE_DETAILS } })
  seed(on, 'prDetails', { 'api#3029': { status: 'ok', fetchedAt: 1, details: DETAILS } })

  for (const surface of SURFACES) {
    // 100 is the narrowest width that puts the boxes side by side, so each is only half the pane wide.
    const ui = await $.ui.mount({ ...paneAt(100), surface })
    const tree = await ui.drawn()
    const noShrink = (n: Node) => n.type === 'Box' && n.props?.flexShrink === 0

    // A label that shrank would be cut mid-word ("Trackin" / "g:"), so each sits in a box that never shrinks.
    for (const label of ['Repo:', 'Tracking:', 'Last commit:', 'Changes:', 'PR #3029']) {
      expect(findNode(tree, n => noShrink(n) && textsUnder(n).join('') === label)).toBeDefined()
    }
    // So do the buttons at the end of a header: a long branch must not squeeze them.
    for (const key of ['close-worktree', 'reload-prs']) {
      expect(findNode(tree, n => noShrink(n) && findNode(n, m => m.props?.key === key) !== undefined)).toBeDefined()
    }

    // Rows of coloured pieces wrap whole pieces onto the next line, never splitting one: the topology row's
    // status line, and the box's Changes row.
    const wrapping = findNodes(tree, n => n.type === 'Box' && n.props?.flexWrap === 'wrap' && textsUnder(n).includes('1 changed'))
    expect(wrapping).toHaveLength(2)
    await ui.unmount()
  }
})

test('a repo whose scan failed says so, now that the repo summary line is not there to', async ($, on) => {
  seed(on, 'model', { ...MODEL_VALUE, repos: [repo('api', { error: 'boom' })] })

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.drawn()
  expect(await ui.find({ type: 'Text', text: /^Scan failed: boom$/ })).toBeDefined()
  await ui.unmount()
})

test('pressing a worktree name opens its box, and Close hides it again', async ($, on) => {
  seed(on, 'model', MODEL_VALUE)
  seed(on, 'prs', SEEDED_PRS)

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: `worktree-${SCRATCH}` })
    await ui.drawn()

    // `scratch` has no PR: its box is the worktree alone.
    expect(await ui.find({ type: 'Button', key: 'close-worktree' })).toBeDefined()
    expect((await ui.find({ type: 'Text', text: /^scratch$/ }))?.props.bold).toBe(true)
    expect((await ui.find({ type: 'Text', text: /^\/ws\/api\/\.worktrees\/scratch$/ }))?.props.dimColor).toBe(true)
    expect(await ui.find({ type: 'Box', key: 'pr-header' })).toBeUndefined()
    // The test environment has no process to run git in, so the details come back as the failure.
    expect(await ui.find({ type: 'Text', text: /Couldn't run git/ })).toBeDefined()

    await ui.press({ key: 'close-worktree' })
    expect(await ui.find({ type: 'Button', key: 'close-worktree' })).toBeUndefined()
    await ui.unmount()
  }
})

test('opening a worktree whose branch has a PR shows the PR under it, in a header box of its own', async ($, on) => {
  seed(on, 'model', MODEL_VALUE)
  seed(on, 'prs', SEEDED_PRS)

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: `worktree-${TOPOLOGY}` })
    const tree = await ui.drawn()

    expect(await ui.find({ type: 'Button', key: 'close-worktree' })).toBeDefined()
    // The worktree's header is the branch and Close; the PR has a header box of its own, below it.
    expect(textsUnder(findNode(tree, n => n.props?.key === 'worktree-header'))).toEqual(['topology'])
    expect(textsUnder(findNode(tree, n => n.props?.key === 'pr-header'))).toEqual(['PR #3029'])
    expect((await ui.find({ type: 'Text', text: /^PR #3029$/ }))?.props.bold).toBe(true)
    // Neither lookup can run in the test environment.
    expect(await ui.find({ type: 'Text', text: /Couldn't run git/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Couldn't run gh/ })).toBeDefined()

    await ui.press({ key: 'close-worktree' })
    await ui.unmount()
  }
})

test('the box shows the worktree and, below it, its PR once they have loaded', async ($, on) => {
  seed(on, 'model', MODEL_VALUE)
  seed(on, 'prs', SEEDED_PRS)
  seed(on, 'openWorktree', { repo: 'api', path: TOPOLOGY })
  seed(on, 'worktreeDetails', { [TOPOLOGY]: { status: 'ok', fetchedAt: 1, details: WORKTREE_DETAILS } })
  seed(on, 'prDetails', { 'api#3029': { status: 'ok', fetchedAt: 1, details: DETAILS } })

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    const tree = await ui.drawn()
    const box = findNode(tree, n => n.props?.key === 'worktree-box')
    const texts = textsUnder(box)

    // The worktree's header: the branch, with Close pushed to the right.
    expect(textsUnder(findNode(tree, n => n.props?.key === 'worktree-header'))).toEqual(['topology'])
    expect((await ui.find({ type: 'Box', key: 'worktree-header' }))?.props.justifyContent).toBe('space-between')
    expect(await ui.find({ type: 'Button', key: 'close-worktree' })).toBeDefined()

    // The path, a blank line, then the tracking line.
    const pathAt = texts.indexOf(TOPOLOGY)
    expect(pathAt).toBeGreaterThanOrEqual(0)
    expect(texts[pathAt + 1]).toBe(' ')
    expect(texts[pathAt + 2]).toBe('Tracking:')
    expect((await ui.find({ type: 'Text', text: new RegExp(`^${TOPOLOGY}$`) }))?.props.dimColor).toBe(true)

    // Each category is a bold label with its value beside it.
    for (const label of ['Tracking:', 'Last commit:', 'Changes:']) {
      expect((await ui.find({ type: 'Text', text: new RegExp(`^${label}$`) }))?.props.bold).toBe(true)
    }

    // The last commit's message is plain dimmed text, like the path, under the line with who and when.
    const message = await ui.find({ type: 'Text', text: /^Fix the thing\n\nWith a longer explanation\.$/ })
    expect(message?.props.dimColor).toBe(true)
    expect(await ui.find({ type: 'Code' })).toBeUndefined()

    // Then, in order: last commit, changes, and the PR with its header box (number, then status).
    const at = (wanted: string | RegExp) => texts.findIndex(t => (typeof wanted === 'string' ? t === wanted : wanted.test(t)))
    const order = [
      TOPOLOGY,
      'Tracking:',
      'origin/topology · ↑2 ↓1',
      'Last commit:',
      /^abc1234 · Jeff Roche, /,
      'Fix the thing\n\nWith a longer explanation.',
      'Changes:',
      '1 changed',
      '2 new',
      '3 deleted',
      'PR #3029',
      'Open',
    ].map(at)
    expect(order.every(index => index >= 0)).toBe(true)
    expect(order).toEqual([...order].sort((a, b) => a - b))
    expect(texts).not.toContain('Pull request')

    const prHeader = findNode(tree, n => n.props?.key === 'pr-header')
    expect(textsUnder(prHeader)).toEqual(['PR #3029', 'Open'])
    expect(findNode(prHeader, n => n.type === 'Text' && textsUnder(n)[0] === 'Open')?.props?.color).toBe('success')
    expect((await ui.find({ type: 'Text', text: /^PR #3029$/ }))?.props.bold).toBe(true)

    // The PR is one markdown block: the title a header, the PR an actual link.
    const markdown = (await ui.find({ type: 'Markdown' }))?.props.text
    expect(markdown).toContain('## Add the thing')
    expect(markdown).not.toContain('**OPEN**')
    expect(markdown).toContain('`topology` → `master` · by jeff-roche · updated ')
    expect(markdown).toContain('- **Unresolved review comments:** 1')
    expect(markdown).toContain('- **Checks:** 30 passed · 1 pending')
    expect(markdown).toContain('- **Changes:** +4582 −78 · 23 files')
    expect(markdown).toContain('[Open on GitHub](https://github.com/org/api/pull/3029)')
    expect(await ui.find({ type: 'Link' })).toBeUndefined()
    await ui.unmount()
  }
})

test('a worktree with a clean tree says none under Changes', async ($, on) => {
  seed(on, 'model', MODEL_VALUE)
  seed(on, 'openWorktree', { repo: 'api', path: SCRATCH })
  seed(on, 'worktreeDetails', {
    [SCRATCH]: {
      status: 'ok',
      fetchedAt: 1,
      details: { ...WORKTREE_DETAILS, upstream: null, ahead: 0, behind: 0, changes: { changed: 0, added: 0, deleted: 0 } },
    },
  })

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const texts = textsUnder(findNode(await ui.drawn(), n => n.props?.key === 'worktree-box'))
  expect(texts).toContain('Tracking:')
  expect(texts).toContain('no upstream')
  expect(texts.slice(texts.indexOf('Changes:'))).toEqual(['Changes:', 'none'])
  await ui.unmount()
})

test('a box left open across a reload, with details saved by the previous version, still draws', async ($, on) => {
  // State outlives a hot reload, so the new code can meet what the old code saved: PR details with a review
  // decision and no unresolved count, and a last commit with a subject and no message.
  const { unresolved: _added, ...savedPr } = DETAILS
  const { message: _renamed, ...savedCommit } = WORKTREE_DETAILS.lastCommit!
  seed(on, 'model', MODEL_VALUE)
  seed(on, 'prs', SEEDED_PRS)
  seed(on, 'openWorktree', { repo: 'api', path: TOPOLOGY })
  seed(on, 'worktreeDetails', {
    [TOPOLOGY]: {
      status: 'ok',
      fetchedAt: 1,
      details: { ...WORKTREE_DETAILS, lastCommit: { ...savedCommit, subject: 'Fix the thing' } },
    },
  })
  seed(on, 'prDetails', { 'api#3029': { status: 'ok', fetchedAt: 1, details: { ...savedPr, reviewDecision: '' } } })

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.drawn()

    expect((await ui.find({ type: 'Text', text: /^\(no message\)$/ }))?.props.dimColor).toBe(true)
    const markdown = (await ui.find({ type: 'Markdown' }))?.props.text
    expect(markdown).toContain('## Add the thing')
    expect(markdown).toContain('- **Unresolved review comments:** unknown')
    await ui.unmount()
  }
})

test('a reload closes the box and drops the saved details, so nothing from an older version outlives it', async ($, on) => {
  seed(on, 'model', MODEL_VALUE)
  seed(on, 'prs', SEEDED_PRS)
  // The test environment has nothing beneath the plugin to answer session.start.
  on('session.start', () => ({ cwd: '/ws' }))

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: `worktree-${TOPOLOGY}` })
  expect(await ui.find({ type: 'Button', key: 'close-worktree' })).toBeDefined()

  await $.session.start({ cwd: '/ws', surface: 'terminal', isInteractive: true })
  await ui.drawn()

  expect(await ui.find({ type: 'Button', key: 'close-worktree' })).toBeUndefined()
  await ui.unmount()
})

test('the repo picker is a dropdown of buttons: the toggle opens its list, a pick closes it and shows that repo', async ($, on) => {
  seed(on, 'model', MODEL_VALUE)

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    const label = async () => (await ui.find({ type: 'Button', key: 'repo-picker-toggle' }))?.props.label
    const option = async (name: string) => (await ui.find({ type: 'Button', key: `repo-${name}` }))?.props

    expect(await label()).toBe('api ▾')
    await ui.press({ key: 'repo-picker-toggle' })

    // Open: every repo is a plain button (no brackets), with the one shown marked by a filled dot.
    expect(await label()).toBe('api ▴')
    expect(await option('api')).toMatchObject({ label: '● api', plain: true })
    expect(await option('oc')).toMatchObject({ label: '○ oc', plain: true })
    expect(await ui.find({ type: 'Text', text: /^Worktrees \(3\)$/ })).toBeDefined()

    await ui.press({ key: 'repo-oc' })

    // A pick closes the list and shows that repo.
    expect(await ui.find({ type: 'Box', key: 'repo-list' })).toBeUndefined()
    expect(await label()).toBe('oc ▾')
    expect(await ui.find({ type: 'Text', text: /^Worktrees \(1\)$/ })).toBeDefined()

    // The pick is kept in state, so put it back for the next surface's pass.
    await ui.press({ key: 'repo-picker-toggle' })
    await ui.press({ key: 'repo-api' })
    await ui.unmount()
  }
})

test('pressing the toggle again closes the list without picking anything', async ($, on) => {
  seed(on, 'model', MODEL_VALUE)

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'repo-picker-toggle' })
  expect(await ui.find({ type: 'Box', key: 'repo-list' })).toBeDefined()

  await ui.press({ key: 'repo-picker-toggle' })
  expect(await ui.find({ type: 'Box', key: 'repo-list' })).toBeUndefined()
  expect((await ui.find({ type: 'Button', key: 'repo-picker-toggle' }))?.props.label).toBe('api ▾')
  await ui.unmount()
})

test('a reload closes the repo list, like the rest of what is only on screen for a moment', async ($, on) => {
  seed(on, 'model', MODEL_VALUE)
  // The test environment has nothing beneath the plugin to answer session.start.
  on('session.start', () => ({ cwd: '/ws' }))

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'repo-picker-toggle' })
  expect(await ui.find({ type: 'Box', key: 'repo-list' })).toBeDefined()

  await $.session.start({ cwd: '/ws', surface: 'terminal', isInteractive: true })
  await ui.drawn()

  expect(await ui.find({ type: 'Box', key: 'repo-list' })).toBeUndefined()
  await ui.unmount()
})

test('the Settings tab draws on every surface, with the status line button saying Disable while it is on', async ($, on) => {
  seed(on, 'model', MODEL_VALUE)

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'tab-settings' })
    await ui.drawn()

    expect((await ui.find({ key: 'toggle-status-line' }))?.props.label).toBe('Disable')
    expect(await ui.find({ type: 'Button', key: 'sync-doc' })).toBeDefined()
    expect(await ui.find({ type: 'Button', key: 'refresh' })).toBeDefined()
    await ui.unmount()
  }
})

test('single-repo mode: no repo picker and no workspace-doc section, but the worktrees still list', async ($, on) => {
  seed(on, 'model', { ...MODEL_VALUE, mode: 'single-repo', repos: [MODEL_VALUE.repos[0]!], otherFolders: [] })
  seed(on, 'prs', SEEDED_PRS)

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.drawn()
    expect(await ui.find({ type: 'Button', key: 'repo-picker-toggle' })).toBeUndefined()
    expect(await ui.find({ type: 'Button', key: `worktree-${TOPOLOGY}` })).toBeDefined()
    await ui.press({ key: 'tab-settings' })
    await ui.drawn()
    expect(await ui.find({ type: 'Button', key: 'sync-doc' })).toBeUndefined()
    expect(await ui.find({ type: 'Button', key: 'refresh' })).toBeDefined()
    // The active tab outlives a mount, so go back for the next surface.
    await ui.press({ key: 'tab-repos' })
    await ui.unmount()
  }
})

test('single-repo mode opened from a linked worktree still lists the main checkout and every other worktree', async ($, on) => {
  const inLinked = repo('api', {
    path: TOPOLOGY,
    branch: 'topology',
    worktrees: [
      { path: '/ws/api', branch: 'master' },
      { path: TOPOLOGY, branch: 'topology' },
      { path: SCRATCH, branch: 'scratch' },
    ],
  })
  seed(on, 'model', { root: TOPOLOGY, mode: 'single-repo', managed: false, projects: [], projectsError: null, repos: [inLinked], otherFolders: [], worktreeGroups: [], scannedAt: 1 })
  seed(on, 'prs', SEEDED_PRS)

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.drawn()
  // The worktree the session is in is the filled dot, the others open ones, as in the repo picker.
  for (const [path, name] of [['/ws/api', '○ master'], [TOPOLOGY, '● topology'], [SCRATCH, '○ scratch']] as const) {
    expect((await ui.find({ type: 'Button', key: `worktree-${path}` }))?.props.label).toBe(name)
  }
  expect(await ui.find({ type: 'Text', text: /Worktrees \(3\)/ })).toBeDefined()
})
