import { expect, test } from 'claude-code/testing'
import type { On } from 'claude-code'

import { MIN_PANE_COLUMNS, paneFitsTerminal, parseWorkspaceCommandArg } from './commands'
import { renderManagedBlock } from './doc'
import type { RepoInfo } from './model'

const SYNC_REPO: RepoInfo = {
  name: 'api', path: '/ws/api', branch: 'main', ahead: 0, behind: 0, dirtyCount: 0, isClean: true,
  defaultBranch: null, remotes: [], worktrees: [{ path: '/ws/api', branch: 'main' }], role: null,
}

function stubWorkspace(on: On, populated: boolean, existing: string): string[] {
  const writes: string[] = []
  on('session.root', () => ({ value: '/ws' }))
  on('fs.read', ($, e) => ({ value: e.path === '/ws/AGENTS.md' ? existing : null }))
  on('fs.list', ($, e) => ({ value: e.path === '/ws' && populated ? [{ name: 'api', kind: 'dir' }] : [] }))
  on('fs.write', ($, e) => (writes.push(e.path), { value: undefined }))
  on('ui.status', () => ({ value: undefined }))
  on('process.run', ($, e) => {
    if (e.init?.cwd !== '/ws/api') return { value: { exitCode: 128, stdout: '', stderr: 'not a repo' } }
    const stdout = e.argv[1] === 'rev-parse' ? '\n/ws/api\n'
      : e.argv[1] === 'status' ? '## main\n'
      : e.argv[1] === 'worktree' ? 'worktree /ws/api\nHEAD abc\nbranch refs/heads/main\n'
      : ''
    return { value: { exitCode: 0, stdout, stderr: '' } }
  })

  return writes
}

test('sync-doc reports an already synced map and writes nothing', async ($, on) => {
  const writes = stubWorkspace(on, true, `${renderManagedBlock([SYNC_REPO])}\n`)

  const result = await $.command.run({ command: 'workspace', args: 'sync-doc' })

  expect(result.text).toContain('already up to date')
  expect(writes).toEqual([])
})

test('sync-doc reports no repos and writes nothing for an empty workspace', async ($, on) => {
  const writes = stubWorkspace(on, false, '# My workspace\n')

  const result = await $.command.run({ command: 'workspace', args: 'sync-doc' })

  expect(result.text).toBe('No repos discovered; nothing to write.')
  expect(writes).toEqual([])
})

test('parseWorkspaceCommandArg: no args opens the pane', async () => {
  expect(parseWorkspaceCommandArg('')).toBe('open')
  expect(parseWorkspaceCommandArg('   ')).toBe('open')
})

test('parseWorkspaceCommandArg: recognizes refresh and sync-doc', async () => {
  expect(parseWorkspaceCommandArg('refresh')).toBe('refresh')
  expect(parseWorkspaceCommandArg(' sync-doc ')).toBe('sync-doc')
})

test('parseWorkspaceCommandArg: an unknown argument falls back to opening the pane', async () => {
  expect(parseWorkspaceCommandArg('bogus')).toBe('open')
})

test('paneFitsTerminal: fullscreen needs the dock width, the main screen always fits', async () => {
  expect(paneFitsTerminal({ isFullscreen: true, columns: MIN_PANE_COLUMNS })).toBe(true)
  expect(paneFitsTerminal({ isFullscreen: true, columns: MIN_PANE_COLUMNS - 1 })).toBe(false)
  expect(paneFitsTerminal({ isFullscreen: false, columns: 60 })).toBe(true)
})
