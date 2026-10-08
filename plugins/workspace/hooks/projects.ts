// The projects of a managed workspace. The files are read by `scripts/projects.py list`, shared with the
// skills, so the mod never parses a project's frontmatter itself: this module only turns the script's
// JSON into the model's shape, and says why when it can't.

import type { ExecResult } from './discovery'
import type { ProjectInfo } from './model'

export type ProjectsResult = { projects: ProjectInfo[]; error: string | null }

const NONE: ProjectsResult = { projects: [], error: null }

const str = (value: unknown): string => (typeof value === 'string' ? value : '')
const count = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0)

/** Pure: the script's stdout as projects, or the reason it is unusable. Never throws. */
export function parseProjectsJson(result: Pick<ExecResult, 'exitCode' | 'stdout' | 'stderr'>): ProjectsResult {
  if (result.exitCode !== 0) {
    const why = result.stderr.trim().split('\n')[0] || `exit ${result.exitCode}`
    return { projects: [], error: `projects.py failed: ${why}` }
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(result.stdout)
  } catch {
    return { projects: [], error: 'projects.py printed something that is not JSON' }
  }
  if (typeof parsed !== 'object' || parsed === null) return { projects: [], error: 'projects.py printed no object' }
  const body = parsed as { status?: unknown; projects?: unknown; error_message?: unknown }
  if (body.status === 'no_workspace') return NONE
  if (body.status !== 'ok' || !Array.isArray(body.projects)) {
    return { projects: [], error: str(body.error_message) || 'projects.py reported a problem' }
  }
  const projects = body.projects.flatMap((raw: unknown): ProjectInfo[] => {
    if (typeof raw !== 'object' || raw === null) return []
    const p = raw as Record<string, unknown>
    const name = str(p.name)
    if (name === '') return []
    const tasks = (typeof p.tasks === 'object' && p.tasks !== null ? p.tasks : {}) as Record<string, unknown>
    return [
      {
        name,
        project: str(p.project) || name,
        type: str(p.type),
        status: str(p.status),
        lastActive: str(p.last_active),
        jira: str(p.jira),
        branch: str(p.branch),
        domain: str(p.domain),
        finished: p.finished === true,
        tasks: { checked: count(tasks.checked), total: count(tasks.total) },
      },
    ]
  })
  return { projects, error: null }
}

/** Reads the projects of the workspace at `root`; a script that cannot run is an error string, not a throw. */
export async function loadProjects(
  exec: (argv: string[], cwd: string, env?: Record<string, string>) => Promise<ExecResult>,
  script: string,
  root: string,
): Promise<ProjectsResult> {
  try {
    return parseProjectsJson(await exec(['python3', script, 'list'], root, { WORKSPACE_ROOT: root }))
  } catch (err) {
    return { projects: [], error: `could not run projects.py: ${err instanceof Error ? err.message : String(err)}` }
  }
}
