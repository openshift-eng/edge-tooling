import { parseProjectDoc } from './project'
import type { RefFile } from './project'
import { getAttached, getSelected, setFiles, setSelection } from './state'
import type { Api, ProjectFile } from './state'

export const PANE = 'workspace-project'
const MAX_DEPTH = 3
const ALWAYS_REGISTERED = ['CLAUDE.md', 'README.md']

export async function listProjectFiles($: Api, dir: string, refFiles: RefFile[]): Promise<ProjectFile[]> {
  const registered = new Set([...ALWAYS_REGISTERED, ...refFiles.map(f => f.path)])
  const out: ProjectFile[] = []
  const walk = async (rel: string, depth: number): Promise<void> => {
    for (const e of await $.fs.list(rel ? `${dir}/${rel}` : dir)) {
      if (e.name.startsWith('.')) continue
      const path = rel ? `${rel}/${e.name}` : e.name
      if (e.kind === 'file') out.push({ path, mtimeMs: e.mtimeMs, isRegistered: registered.has(path) })
      else if (e.kind === 'dir' && depth < MAX_DEPTH) await walk(path, depth + 1)
    }
  }
  await walk('', 1)
  return out.sort((a, b) => b.mtimeMs - a.mtimeMs)
}

async function readPreview($: Api, dir: string, path: string): Promise<string> {
  try {
    return String(await $.fs.read(`${dir}/${path}`))
  } catch {
    return ''
  }
}

export async function selectFile($: Api, path: string): Promise<void> {
  const a = await getAttached($)
  if (!a) return
  await setSelection($, path, await readPreview($, a.dir, path))
}

export async function refreshFiles($: Api): Promise<void> {
  const a = await getAttached($)
  if (!a) return
  const doc = parseProjectDoc(String(await $.fs.read(`${a.dir}/CLAUDE.md`)))
  const list = await listProjectFiles($, a.dir, doc.refFiles)
  await setFiles($, list)
  const current = await getSelected($)
  // A save often rewrites the selected file, so its preview is reloaded too.
  if (current !== null && list.some(f => f.path === current)) {
    await setSelection($, current, await readPreview($, a.dir, current))
    return
  }
  const first = list[0]?.path ?? null
  await setSelection($, first, first === null ? '' : await readPreview($, a.dir, first))
}

export function atPath(fileAbs: string, cwd: string): string {
  const root = cwd.replace(/\/+$/, '')
  return fileAbs.startsWith(`${root}/`) ? `@${fileAbs.slice(root.length + 1)}` : fileAbs
}

export async function insertSelected($: Api): Promise<void> {
  const a = await getAttached($)
  const path = await getSelected($)
  if (!a || path === null) return
  await $.prompt.fill({ text: `${atPath(`${a.dir}/${path}`, await $.session.cwd())} `, mode: 'insert' })
}

export async function openPane($: Api): Promise<{ text: string }> {
  const a = await getAttached($)
  if (!a) return { text: 'No workspace project is attached. Run /workspace:resume-project.' }
  await refreshFiles($)
  await $.ui.open({ id: PANE, title: a.name, focus: true, closeOnEscape: true })
  return { text: 'Project pane opened.' }
}

