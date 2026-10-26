import { expect, test } from 'claude-code/testing'

import { loadProjects, parseProjectsJson } from './projects'

const ok = (body: unknown) => ({ exitCode: 0, stdout: JSON.stringify(body), stderr: '' })

test('loadProjects selects the workspace root in cwd and env', async () => {
  const calls: { argv: string[]; cwd: string; env?: Record<string, string> }[] = []
  const exec = async (argv: string[], cwd: string, env?: Record<string, string>) => {
    calls.push({ argv, cwd, env })
    return ok({ status: 'ok', projects: [{ name: 'fix-it' }] })
  }

  const result = await loadProjects(exec, '/plugin/scripts/projects.py', '/chosen/ws')

  expect(calls).toEqual([{
    argv: ['python3', '/plugin/scripts/projects.py', 'list'], cwd: '/chosen/ws', env: { WORKSPACE_ROOT: '/chosen/ws' },
  }])
  expect(result.projects.map(p => p.name)).toEqual(['fix-it'])
  expect(result.error).toBeNull()
})

test('parseProjectsJson maps the script output to projects', async () => {
  const { projects, error } = parseProjectsJson(
    ok({
      status: 'ok',
      projects: [
        { name: 'fix-it', project: 'Fix it', type: 'bug', status: 'active', last_active: '2026-10-01T10:00', jira: 'u', branch: 'b', domain: 'tnf', finished: false, tasks: { checked: 2, total: 5 } },
        { name: 'old', status: 'done', finished: true },
      ],
    }),
  )
  expect(error).toBeNull()
  expect(projects[0]).toEqual({
    name: 'fix-it', project: 'Fix it', type: 'bug', status: 'active', lastActive: '2026-10-01T10:00', jira: 'u', branch: 'b', domain: 'tnf', finished: false, tasks: { checked: 2, total: 5 },
  })
  // Missing keys fall back instead of failing the whole list.
  expect(projects[1]).toMatchObject({ name: 'old', project: 'old', type: '', lastActive: '', finished: true, tasks: { checked: 0, total: 0 } })
})

test('parseProjectsJson skips entries without a name and ignores junk counts', async () => {
  const { projects } = parseProjectsJson(ok({ status: 'ok', projects: [null, 3, { type: 'bug' }, { name: 'a', tasks: { checked: -1, total: 'x' } }] }))
  expect(projects.map(p => p.name)).toEqual(['a'])
  expect(projects[0]!.tasks).toEqual({ checked: 0, total: 0 })
})

test('parseProjectsJson: no workspace is an empty list, not an error', async () => {
  expect(parseProjectsJson(ok({ status: 'no_workspace', projects: [] }))).toEqual({ projects: [], error: null })
})

test('parseProjectsJson explains a failing or garbled script', async () => {
  expect(parseProjectsJson({ exitCode: 2, stdout: '', stderr: 'boom\nmore' }).error).toBe('projects.py failed: boom')
  expect(parseProjectsJson({ exitCode: 0, stdout: 'not json', stderr: '' }).error).toContain('not JSON')
  expect(parseProjectsJson(ok({ status: 'error', error_message: 'bad root' })).error).toBe('bad root')
  expect(parseProjectsJson(ok([])).error).toBeTruthy()
})
