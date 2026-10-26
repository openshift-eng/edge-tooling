export type Frontmatter = Record<string, string>
export type RefFile = { path: string; description: string }
export type ProjectDoc = {
  frontmatter: Frontmatter
  progress: { done: number; total: number }
  refFiles: RefFile[]
}

const FENCE = /^---\s*$/
const LEVEL2 = /^##\s/

function frontmatterRange(lines: string[]): [number, number] | null {
  if (!lines.length || !FENCE.test(lines[0])) return null
  const end = lines.findIndex((l, i) => i > 0 && FENCE.test(l))
  return end === -1 ? null : [0, end]
}

function parseFrontmatter(lines: string[]): Frontmatter {
  const range = frontmatterRange(lines)
  const out: Frontmatter = {}
  if (!range) return out
  for (const line of lines.slice(1, range[1])) {
    const m = line.match(/^([A-Za-z0-9_-]+):[ \t]*(.*)$/)
    if (m && m[2].trim() !== '') out[m[1]] = m[2].trim()
  }
  return out
}

function section(lines: string[], heading: RegExp): string[] {
  const start = lines.findIndex(l => heading.test(l))
  if (start === -1) return []
  const rest = lines.slice(start + 1)
  const end = rest.findIndex(l => LEVEL2.test(l))
  return end === -1 ? rest : rest.slice(0, end)
}

function cellPath(cell: string): string {
  const link = cell.match(/^\[[^\]]*\]\(([^)]+)\)$/)
  return (link ? link[1] : cell).trim().replace(/^`+|`+$/g, '')
}

function parseRefFiles(lines: string[]): RefFile[] {
  const rows = section(lines, /^##\s+Reference Files/i).filter(l => l.includes('|'))
  const out: RefFile[] = []
  let sawHeader = false
  for (const row of rows) {
    if (!sawHeader) {
      sawHeader = true
      continue
    }
    if (/^\|[-\s|:]+\|?$/.test(row)) continue
    const cells = row.split('|').map(c => c.trim()).filter(Boolean)
    if (cells.length >= 2) out.push({ path: cellPath(cells[0]), description: cells[1] })
  }
  return out
}

function parseProgress(lines: string[]): { done: number; total: number } {
  let done = 0
  let total = 0
  for (const line of section(lines, /^##\s+Progress\s*$/i)) {
    const m = line.match(/^\s*- \[([ x])\] /)
    if (!m) continue
    total++
    if (m[1] === 'x') done++
  }
  return { done, total }
}

export function parseProjectDoc(text: string): ProjectDoc {
  const lines = text.split('\n')
  return {
    frontmatter: parseFrontmatter(lines),
    progress: parseProgress(lines),
    refFiles: parseRefFiles(lines),
  }
}

export function setFrontmatterField(text: string, key: string, value: string): string {
  const lines = text.split('\n')
  const range = frontmatterRange(lines)
  if (!range) return text
  const [, end] = range
  const newLine = `${key}: ${value}`
  const prefix = `${key}:`
  const existing = lines.findIndex((l, i) => i > 0 && i < end && l.startsWith(prefix))
  if (existing !== -1) {
    lines[existing] = newLine
  } else {
    const status = lines.findIndex((l, i) => i > 0 && i < end && l.startsWith('status:'))
    lines.splice(status === -1 ? end : status + 1, 0, newLine)
  }
  return lines.join('\n')
}

export function leanIndexRules(skillText: string): string {
  return section(skillText.split('\n'), /^##\s+Lean Index Rules\s*$/i).join('\n')
}
