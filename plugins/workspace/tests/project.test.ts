import { expect, test } from 'claude-code/testing'

import { leanIndexRules, parseProjectDoc, setFrontmatterField } from '../hooks/project'

const DOC = `---
project: demo
type: feature
status: active
last-active: 2026-10-21T18:00
repos: []
---
# Demo
## Reference Files
| File | Content |
|------|---------|
| [design.md](design.md) | Design |
| \`notes/run.md\` | Runs |
## Implementation Plan
- [ ] not counted
## Progress
- [x] Project created
- [ ] Tests written
- [x] Design done
`
test('parses frontmatter, progress and reference files', async () => {
  const d = parseProjectDoc(DOC)
  expect(d.frontmatter.type).toBe('feature')
  expect(d.frontmatter.repos).toBe('[]')
  expect(d.progress).toEqual({ done: 2, total: 3 })
  expect(d.refFiles.map(f => f.path)).toEqual(['design.md', 'notes/run.md'])
})
test('parses a doc with no frontmatter or progress', async () => {
  expect(parseProjectDoc('# Just a title\n')).toEqual({ frontmatter: {}, progress: { done: 0, total: 0 }, refFiles: [] })
})
test('sets an existing field and inserts a missing one after status', async () => {
  const a = setFrontmatterField(DOC, 'last-active', '2026-10-22T09:00')
  expect(a).toContain('last-active: 2026-10-22T09:00\n')
  const b = setFrontmatterField(DOC, 'current-task', 'Write plan')
  expect(b).toContain('status: active\ncurrent-task: Write plan\n')
  expect(setFrontmatterField('# none\n', 'x', 'y')).toBe('# none\n')
})
test('extracts the lean index rules section', async () => {
  const skill = '# T\n## Lean Index Rules\n\n- One line.\n## Step 1\nx\n'
  expect(leanIndexRules(skill).trim()).toBe('- One line.')
})
