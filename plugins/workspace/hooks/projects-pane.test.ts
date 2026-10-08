import { expect, test } from 'claude-code/testing'

import type { On } from 'claude-code'

import type { ProjectInfo, RepoInfo, WorkspaceModel } from './model'

// The Projects and Actions tabs on every surface, with the scripts they run answered by stubs.

const PANE = {
  plugin: 'workspace',
  component: 'Pane',
  requestId: 'workspace',
  props: { title: 'Workspace', isFocused: false, bodyColumns: 80, placement: 'inline', scroll: { offset: 0, bodyRows: 24 }, view: {} },
} as const

const SURFACES = ['terminal', 'desktop', 'vscode', 'mobile'] as const

const repo = (name: string): RepoInfo => ({
  name, path: `/ws/repos/${name}`, branch: 'main', ahead: 0, behind: 0, dirtyCount: 0, isClean: true, defaultBranch: 'main',
  remotes: [{ name: 'origin', url: 'u', role: 'primary' }], worktrees: [{ path: `/ws/repos/${name}`, branch: 'main' }], role: null,
})

const project = (name: string, overrides: Partial<ProjectInfo> = {}): ProjectInfo => ({
  name, project: name, type: 'bug', status: 'active', lastActive: '2026-10-01T10:00', jira: '', branch: '', domain: '', finished: false,
  tasks: { checked: 1, total: 3 }, ...overrides,
})

const model = (overrides: Partial<WorkspaceModel> = {}): WorkspaceModel => ({
  root: '/ws', mode: 'workspace', managed: true, projects: [project('fix-it'), project('old', { status: 'done', finished: true })],
  projectsError: null, repos: [repo('api')], otherFolders: [], worktreeGroups: [], scannedAt: 1, ...overrides,
})

// The model is seeded at a fixed version, so the plugin's own writes to it cannot land; such a failure is
// toasted by the pane, which a test answers here like any other toast.
const toasts: string[] = []
const seedModel = (on: On, value: WorkspaceModel) => {
  toasts.length = 0
  on('ui.toast', ($, e) => (toasts.push(e.text), { value: undefined }))
  seedFixed(on, value)
}
const seedFixed = (on: On, value: WorkspaceModel) =>
  on('state.get', { plugin: 'workspace', key: 'model' } as { plugin: 'workspace'; key: 'model' }, () => ({ value: { value, version: 1 } }))

const TASKS = {
  status: 'ok',
  project: 'fix-it',
  sections: [
    { heading: 'Fix Plan', level: 2, items: [{ text: 'Find root cause', checked: false, occurrence: 0 }, { text: 'Write fix', checked: true, occurrence: 0 }] },
    { heading: 'Progress', level: 2, items: [{ text: 'Project created', checked: true, occurrence: 0 }] },
  ],
}

type Call = { argv: readonly string[]; cwd?: string; env?: Record<string, string>; stdin?: string }

/** Answers every `$.process.run` from `answer(script, args)`, recording each call. */
function stubScripts(on: On, answer: (script: string, args: string[]) => unknown): Call[] {
  const calls: Call[] = []
  on('session.root', () => ({ value: '/ws' }))
  on('process.run', ($, e) => {
    calls.push({ argv: e.argv, ...e.init })
    const [, script = '', ...args] = e.argv as string[]
    return { value: { exitCode: 0, stdout: JSON.stringify(answer(script.split('/').pop() ?? '', args)), stderr: '' } }
  })
  return calls
}

const stubLaunches = (on: On) => {
  const launched: { command: string; args?: string }[] = []
  on('command.run', ($, e) => {
    launched.push({ command: e.command, args: e.args })
    return { text: '' }
  })
  return launched
}

// The mounted pane's handle, as far as these tests use it.
type Pane = { press: (arg: { key: string }) => Promise<unknown>; drawn: () => Promise<unknown> }

const openTab = async (ui: Pane, tab: string) => {
  await ui.press({ key: `tab-${tab}` })
  await ui.drawn()
}

test('an unmanaged folder offers to set the workspace up, and the button starts that skill', async ($, on) => {
  seedModel(on, model({ managed: false, projects: [] }))
  const launched = stubLaunches(on)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await openTab(ui, 'projects')
    expect(await ui.find({ type: 'Text', text: /Not a managed workspace/ })).toBeDefined()
    await ui.press({ key: 'setup-environment' })
    await ui.press({ key: 'tab-repos' })
    await ui.unmount()
  }
  expect(launched[0]).toEqual({ command: 'workspace:setup-environment', args: '' })
})

test('the Projects tab lists each project with skill buttons, and a finished one can only be resumed', async ($, on) => {
  seedModel(on, model())
  const launched = stubLaunches(on)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await openTab(ui, 'projects')
    expect((await ui.find({ key: 'project-fix-it' }))?.props.label).toBe('▸ fix-it')
    expect(await ui.find({ type: 'Text', text: /^bug · active · active .+ · 1\/3 tasks$/ })).toBeDefined()
    for (const key of ['resume-project-fix-it', 'update-project-fix-it', 'handoff-fix-it', 'consolidate-fix-it', 'close-fix-it']) {
      expect(await ui.find({ type: 'Button', key })).toBeDefined()
    }
    expect(await ui.find({ type: 'Button', key: 'resume-project-old' })).toBeDefined()
    for (const key of ['update-project-old', 'handoff-old', 'consolidate-old', 'close-old']) {
      expect(await ui.find({ type: 'Button', key })).toBeUndefined()
    }
    await ui.press({ key: 'resume-project-fix-it' })
    await ui.press({ key: 'tab-repos' })
    await ui.unmount()
  }
  expect(launched[0]).toEqual({ command: 'workspace:resume-project', args: 'fix-it' })
})

test('opening a project loads its tasks; pressing one toggles it through the shared script', async ($, on) => {
  seedModel(on, model())
  const calls = stubScripts(on, (script, args) =>
    script === 'project-tasks.py' && args[0] === 'list' ? TASKS : script === 'projects.py' ? { status: 'ok', projects: [] } : { status: 'ok', checked: true },
  )
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await openTab(ui, 'projects')
  await ui.press({ key: 'project-fix-it' })
  await ui.drawn()
  expect(await ui.find({ type: 'Text', text: 'Tasks (2/3)' })).toBeDefined()
  // Open items first; finished ones stay hidden until asked for.
  expect((await ui.find({ key: 'task-toggle-0-0' }))?.props.label).toBe('☐ Find root cause')
  expect(await ui.find({ key: 'task-toggle-0-1' })).toBeUndefined()
  await ui.press({ key: 'tasks-show-done' })
  expect((await ui.find({ key: 'task-toggle-0-1' }))?.props.label).toBe('☑ Write fix')

  await ui.press({ key: 'task-toggle-0-0' })
  const edit = calls.find(c => c.argv[2] === 'toggle')
  expect(edit?.argv.slice(2)).toEqual(['toggle', 'fix-it', '--section', 'Fix Plan', '--text', 'Find root cause', '--occurrence', '0'])
  expect(edit?.env).toEqual({ WORKSPACE_ROOT: '/ws' })
  const reload = calls.find(c => c.argv[1]?.endsWith('/projects.py'))
  expect(reload?.cwd).toBe('/ws')
  expect(reload?.env).toEqual({ WORKSPACE_ROOT: '/ws' })
  await ui.unmount()
})

test('adding and removing a task goes through the script, and a stale answer is toasted', async ($, on) => {
  seedModel(on, model())
  const calls = stubScripts(on, (script, args) => {
    if (script === 'project-tasks.py' && args[0] === 'list') return TASKS
    if (script === 'project-tasks.py' && args[0] === 'remove') return { status: 'stale' }
    return { status: 'ok', projects: [] }
  })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await openTab(ui, 'projects')
  await ui.press({ key: 'project-fix-it' })
  await ui.input({ key: 'task-add-0', text: ' Ship it ' })
  expect(calls.find(c => c.argv[2] === 'add')?.argv.slice(2)).toEqual(['add', 'fix-it', '--section', 'Fix Plan', '--text', 'Ship it'])
  await ui.press({ key: 'task-remove-0-0' })
  expect(toasts.some(t => t.includes('changed on disk'))).toBe(true)
  await ui.unmount()
})

test('mobile draws no text fields, so a task list has no add field there', async ($, on) => {
  seedModel(on, model())
  stubScripts(on, (script, args) => (script === 'project-tasks.py' && args[0] === 'list' ? TASKS : { status: 'ok', projects: [] }))
  const ui = await $.ui.mount({ ...PANE, surface: 'mobile' })
  await openTab(ui, 'projects')
  await ui.press({ key: 'project-fix-it' })
  await ui.drawn()
  expect(await ui.find({ key: 'task-toggle-0-0' })).toBeDefined()
  expect(await ui.find({ key: 'task-add-0' })).toBeUndefined()
  await ui.unmount()
})

test('consolidate previews a dry run, then archives on Confirm', async ($, on) => {
  seedModel(on, model())
  const calls = stubScripts(on, (script, args) => {
    if (script === 'consolidate-project.py' && args[0] === '--dry-run') {
      return { status: 'needs_consolidation', claude_md_lines: 120, sections: [{ name: 'Progress', checked: 12, to_archive: 9, to_keep: 3, unchecked: 2, strikethrough: 0 }] }
    }
    if (script === 'consolidate-project.py') return { status: 'consolidated', sections: [{ name: 'Progress', archived: 9, kept: 3 }], claude_md_before: 120, claude_md_after: 60 }
    return script === 'project-tasks.py' ? TASKS : { status: 'ok', projects: [] }
  })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await openTab(ui, 'projects')
  await ui.press({ key: 'consolidate-fix-it' })
  await ui.drawn()
  expect(await ui.find({ type: 'Text', text: '• Progress: archive 9, keep 3 (2 open)' })).toBeDefined()
  // A dry run changed nothing yet.
  expect(calls.filter(c => c.argv[1]?.toString().endsWith('consolidate-project.py') && !c.argv.includes('--dry-run'))).toEqual([])
  await ui.press({ key: 'consolidate-confirm' })
  await ui.drawn()
  expect(await ui.find({ type: 'Text', text: /Archived 9 items; CLAUDE.md went from 120 to 60 lines/ })).toBeDefined()
  await ui.unmount()
})

test('close shows what a removal would lose, takes notes, and applies with the chosen mode', async ($, on) => {
  seedModel(on, model())
  const calls = stubScripts(on, (script, args) => {
    if (script === 'close-project.py' && args[0] === 'check') {
      return { status: 'ok', already_done: false, worktrees: [{ repo: 'api', path: '/ws/repos/api/.worktrees/x', branch: 'x', dirty: true, dirty_files: 2, ahead: 0, no_upstream: false }] }
    }
    if (script === 'close-project.py') return { status: 'ok', removed: [], kept: ['api'], errors: [] }
    return script === 'project-tasks.py' ? TASKS : { status: 'ok', projects: [] }
  })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await openTab(ui, 'projects')
  await ui.press({ key: 'close-fix-it' })
  await ui.drawn()
  expect(await ui.find({ type: 'Text', text: /api · x — dirty \(2 files\)/ })).toBeDefined()
  await ui.input({ key: 'close-notes', text: 'Shipped in PR 12', kind: 'change' })
  await ui.press({ key: 'close-confirm' })
  const apply = calls.find(c => c.argv[2] === 'apply')
  // Dirty work defaults to being kept; nothing is discarded unless asked.
  expect(apply?.argv.slice(2)).toEqual(['apply', 'fix-it', '--notes', 'Shipped in PR 12', '--worktrees', 'keep'])
  await ui.unmount()
})

test('the new-project form follows the description with a branch, and Create sends the payload on stdin', async ($, on) => {
  seedModel(on, model())
  const calls = stubScripts(on, (script, args) =>
    script === 'new-project.py' ? { status: 'ok', folder: 'fix-etcd', path: '/ws/projects/fix-etcd', worktrees: [{}], errors: [] }
      : script === 'project-tasks.py' ? TASKS : { status: 'ok', projects: [] },
  )
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await openTab(ui, 'projects')
  await ui.press({ key: 'new-project-open' })
  await ui.input({ key: 'np-description', text: 'Fix etcd member removal', kind: 'change' })
  expect((await ui.find({ key: 'np-branch' }))?.props.value).toBe('fix-etcd-member-removal')
  await ui.press({ key: 'np-type-feature' })
  await ui.press({ key: 'np-repo-api' })
  await ui.press({ key: 'np-create' })
  const create = calls.find(c => c.argv[2] === 'create')
  expect(JSON.parse(create?.stdin ?? '{}')).toEqual({
    description: 'Fix etcd member removal', type: 'feature', jira: 'none', repos: ['api'], branch: 'fix-etcd-member-removal',
  })
  expect(toasts.some(t => t.startsWith('Created project fix-etcd with 1 worktree'))).toBe(true)
  await ui.unmount()
})

test('the Actions tab launches the workspace-level skills, and New project opens the form', async ($, on) => {
  seedModel(on, model())
  const launched = stubLaunches(on)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await openTab(ui, 'actions')
    for (const skill of ['setup-environment', 'create-domain', 'update-domain', 'auto-update']) {
      expect(await ui.find({ type: 'Button', key: `action-${skill}-button` })).toBeDefined()
    }
    await ui.press({ key: 'action-create-domain-button' })
    await ui.press({ key: 'tab-repos' })
    await ui.unmount()
  }
  expect(launched[0]).toEqual({ command: 'workspace:create-domain', args: '' })
})
