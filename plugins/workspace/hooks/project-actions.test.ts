import { expect, test } from 'claude-code/testing'

import {
  EMPTY_NEW_PROJECT,
  canCreateProject,
  closeApplyArgs,
  closeToast,
  launchToast,
  needsAttention,
  newProjectPayload,
  parseCloseApply,
  parseCloseCheck,
  parseConsolidation,
  parseNewProject,
  parseTaskEdit,
  parseTasks,
  skillInvocation,
  suggestBranch,
  taskEditArgs,
  toggleRepo,
  worktreeStatusText,
} from './project-actions'

const ok = (body: unknown) => ({ exitCode: 0, stdout: JSON.stringify(body), stderr: '' })

test('skillInvocation namespaces the skill and trims the arguments', async () => {
  expect(skillInvocation('resume-project', ' fix-it ')).toEqual({ command: 'workspace:resume-project', args: 'fix-it' })
  expect(skillInvocation('setup-environment')).toEqual({ command: 'workspace:setup-environment', args: '' })
  expect(launchToast(skillInvocation('handoff', 'fix-it'))).toBe('Running /workspace:handoff fix-it once Claude is idle')
  expect(launchToast(skillInvocation('new-project'))).toBe('Running /workspace:new-project once Claude is idle')
})

test('parseTasks reads sections and items, and says why when it cannot', async () => {
  const result = parseTasks(
    ok({
      status: 'ok',
      sections: [
        { heading: 'Fix Plan', level: 2, items: [{ text: 'Find it', checked: true, occurrence: 0 }, { text: '', checked: false }] },
        { heading: 'Progress', level: 2, items: [{ text: 'Done', checked: false, occurrence: 1 }] },
        'junk',
      ],
    }),
  )
  expect(result).toEqual({
    ok: true,
    sections: [
      { heading: 'Fix Plan', level: 2, items: [{ text: 'Find it', checked: true, occurrence: 0 }] },
      { heading: 'Progress', level: 2, items: [{ text: 'Done', checked: false, occurrence: 1 }] },
    ],
  })
  expect(parseTasks(ok({ status: 'not_found' }))).toMatchObject({ ok: false })
  expect(parseTasks({ exitCode: 1, stdout: '', stderr: 'no python' })).toEqual({ ok: false, message: 'project-tasks.py failed: no python' })
  expect(parseTasks({ exitCode: 0, stdout: 'x', stderr: '' })).toMatchObject({ ok: false })
})

test('taskEditArgs passes the section, text and occurrence as separate arguments', async () => {
  expect(taskEditArgs('p', { kind: 'toggle', section: 'Fix Plan', text: 'a; rm -rf /', occurrence: 1 })).toEqual([
    'toggle', 'p', '--section', 'Fix Plan', '--text', 'a; rm -rf /', '--occurrence', '1',
  ])
  expect(taskEditArgs('p', { kind: 'add', section: 'Progress', text: 'new' })).toEqual(['add', 'p', '--section', 'Progress', '--text', 'new'])
})

test('parseTaskEdit distinguishes done, stale and failed', async () => {
  expect(parseTaskEdit(ok({ status: 'ok', checked: true }))).toBe('ok')
  expect(parseTaskEdit(ok({ status: 'stale' }))).toBe('stale')
  expect(parseTaskEdit(ok({ status: 'error', error_message: 'single line only' }))).toEqual({ ok: false, message: 'single line only' })
})

test('parseConsolidation covers the preview, the notes, the outcome and a failure', async () => {
  expect(
    parseConsolidation(ok({ status: 'needs_consolidation', claude_md_lines: 120, sections: [{ name: 'Progress', checked: 12, to_archive: 9, to_keep: 3, unchecked: 2, strikethrough: 0 }] })),
  ).toEqual({ ok: true, kind: 'preview', lines: 120, sections: [{ name: 'Progress', checked: 12, toArchive: 9, toKeep: 3, unchecked: 2, strikethrough: 0 }] })
  expect(parseConsolidation(ok({ status: 'already_lean', error: 'Nothing to consolidate (3 items).' }))).toEqual({ ok: true, kind: 'note', message: 'Nothing to consolidate (3 items).' })
  expect(parseConsolidation(ok({ status: 'consolidated', sections: [{ archived: 9 }, { archived: 2 }], claude_md_before: 120, claude_md_after: 60 }))).toEqual({ ok: true, kind: 'done', archived: 11, before: 120, after: 60 })
  expect(parseConsolidation(ok({ status: 'error', error: 'No CLAUDE.md' }))).toEqual({ ok: false, message: 'No CLAUDE.md' })
})

test('parseCloseCheck reads worktrees and the attention rule flags dirty, ahead and untracked ones', async () => {
  const check = parseCloseCheck(
    ok({
      status: 'ok',
      already_done: false,
      worktrees: [
        { repo: 'api', path: '/ws/repos/api/.worktrees/x', branch: 'x', dirty: true, dirty_files: 2, ahead: 1, no_upstream: false },
        { repo: 'oc', path: '/ws/repos/oc/.worktrees/x', branch: 'x', dirty: false, dirty_files: 0, ahead: 0, no_upstream: false },
        { repo: 'bad' },
      ],
    }),
  )
  if (!check.ok) throw new Error('expected ok')
  expect(check.worktrees.map(w => w.repo)).toEqual(['api', 'oc'])
  expect(check.worktrees.map(needsAttention)).toEqual([true, false])
  expect(worktreeStatusText(check.worktrees[0]!)).toBe('dirty (2 files), ahead by 1')
  expect(worktreeStatusText(check.worktrees[1]!)).toBe('clean')
  expect(parseCloseCheck(ok({ status: 'not_found' }))).toEqual({ ok: false, message: 'Project not found.' })
})

test('closeApplyArgs leaves empty notes out; parseCloseApply and closeToast summarise the outcome', async () => {
  expect(closeApplyArgs('p', '  ', 'keep')).toEqual(['apply', 'p', '--worktrees', 'keep'])
  expect(closeApplyArgs('p', ' shipped ', 'discard')).toEqual(['apply', 'p', '--notes', 'shipped', '--worktrees', 'discard'])
  const done = parseCloseApply(ok({ status: 'ok', removed: ['a', 'b'], kept: ['c'], errors: ['x failed'] }))
  expect(done).toEqual({ ok: true, removed: 2, kept: 1, errors: ['x failed'] })
  if (done.ok) expect(closeToast(done)).toBe('Project closed · 2 worktrees removed · 1 kept · 1 problem: x failed.')
  expect(closeToast({ removed: 0, kept: 0, errors: [] })).toBe('Project closed.')
})

test('the new-project form: branch suggestion, payload, and what lets Create run', async () => {
  expect(suggestBranch('Fix etcd: member removal fails!')).toBe('fix-etcd-member-removal-fails')
  expect(suggestBranch('   ')).toBe('')
  expect(suggestBranch('a'.repeat(60)).length).toBe(40)
  expect(canCreateProject(EMPTY_NEW_PROJECT)).toBe(false)
  expect(canCreateProject({ description: 'x', status: 'creating' })).toBe(false)
  expect(canCreateProject({ description: 'x', status: 'editing' })).toBe(true)
  expect(JSON.parse(newProjectPayload({ ...EMPTY_NEW_PROJECT, description: ' Fix it ', repos: ['api'], branch: ' fix-it ' }))).toEqual({
    description: 'Fix it', type: 'bug', jira: 'none', repos: ['api'], branch: 'fix-it',
  })
  // No worktree is asked for in as many words, because the script otherwise defaults a branch.
  expect(JSON.parse(newProjectPayload({ ...EMPTY_NEW_PROJECT, description: 'x', jira: ' https://j/1 ' }))).toEqual({
    description: 'x', type: 'bug', jira: 'https://j/1', repos: [], no_worktree: true,
  })
  expect(toggleRepo(['a'], 'b')).toEqual(['a', 'b'])
  expect(toggleRepo(['a', 'b'], 'a')).toEqual(['b'])
})

test('parseNewProject reports the folder, worktrees and non-fatal errors', async () => {
  expect(parseNewProject(ok({ status: 'ok', folder: 'fix-it', worktrees: [{}, {}], errors: ['one repo failed'] }))).toEqual({ ok: true, folder: 'fix-it', worktrees: 2, errors: ['one repo failed'] })
  expect(parseNewProject(ok({ status: 'error', error_message: 'no workspace' }))).toEqual({ ok: false, message: 'no workspace' })
  expect(parseNewProject(ok({ status: 'ok' }))).toMatchObject({ ok: false })
})
