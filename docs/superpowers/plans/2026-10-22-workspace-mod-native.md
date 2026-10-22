# Workspace Mod-Native Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a hooks module to `plugins/workspace/` that attaches a project
to the session, shows it (status line, pane, injected context), tracks the
current task, and auto-saves project notes; drop the cron-based
`auto-update` skill.

**Architecture:** One TypeScript hooks module (`hooks/register.ts`) wires
hooks; logic lives in small files by responsibility (pure parsing, attachment,
save pipeline, UI). Session state is in `$.state` atoms; the only cross-clear
state is one `$.store` marker. Saves run as fork-detect → hidden subagent
applies → toast. Skills stay prompt-driven and call four model tools the mod
registers.

**Tech Stack:** Claude Code mod API 2.1.306 (`claude-code`,
`claude-code/testing`), TypeScript/TSX, Python 3.9+ stdlib for existing
scripts.

**Spec:** `docs/superpowers/specs/2026-10-21-workspace-mod-native-design.md`

## Global Constraints

- Mods have no Node/DOM; all I/O through `$` (`$.fs`, `$.process`, `$.clock`, `$.model`, `$.agent`, `$.ui`, `$.store`).
- Never auto-attach on a fresh process (startup, `--resume`, `--continue`).
- Auto-save never toasts, writes, or calls a model when no main-thread turn completed since the last save (manual save excepted).
- Auto-save edits auto-allowed **only** for `*.md` under `projects/<name>/`; never touches `status:`, memory files, or repo sources. No blanket `acceptEdits`.
- Lean-index rules apply to every save verbatim from `skills/update-project/SKILL.md` (`## Lean Index Rules`), including the 100-line cap.
- Every hook fails open: on its own error it calls `next(e)` (or returns the engine's result); it never denies, blocks a turn, compaction, `/clear` or exit.
- No hard dependency on `next`: the `next:go` hook is inert when that command never runs.
- `userConfig`: `autoSave` boolean default `true`; `idleMinutes` number default `50`.
- Plugin degrades on Claude Code without function hooks: skills still work (tools absent → skills fall back).
- Version 0.3.0 → 0.4.0 in `plugins/workspace/.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`.
- Follow `plugins/next/` layout: `hooks/hooks.json` `modules`, `types/index.d.ts` with `PluginState`, `tests/*.test.ts(x)`, `tsconfig.json` extending `./.claude-plugin/types/tsconfig.json`.
- Python helpers stay stdlib-only; existing `plugins/workspace/tests/` suites stay green.

## API facts the tasks rely on (Claude Code 2.1.306)

Read from the bundled `plugin-authoring/types/claude-code.d.ts`; implementers
grep it for anything below that a test contradicts.

- `$.tool.register({name, description, inputSchema, isDeferred: false})` → tool `mcp__workspace__<name>`; must be called in `session.start`. Handle with `on('tool.call', {tool: 'mcp__workspace__<name>'}, …)`; args are flat on `e`; return `{result}` or `{deny}`.
- `tool.check` input `{tool, input, agentId?}`; return `{decision: 'allow'}` or `next(e)`. `agentId` absent on the main loop.
- `$.model.fork({prompt})` → `{isAnswered: true, text, usage}` or `{isAnswered: false, reason}`; `reason: 'nothing-to-fork'` after `/clear` or before the first turn.
- `$.agent.register(AgentSpec)` in `session.start` (type `workspace:saver`); `$.agent.spawn({prompt, subagentType, description})` resolves at start with `{agentId}`; the result arrives as `turn.complete` with `e.agentId === agentId`, answer in `e.answer`. Hide from the model with `on('agent.offer', {agent: 'workspace:saver'}, () => ({isOffered: false}))`.
- `session.end` `{reason: 'clear'|'resume'|'logout'|'prompt_input_exit'|'other'}`, chain budget 1.5 s. **No `session.start` after `/clear`.** `session.start` fires once per process (and on hot reload).
- `session.compact` `{trigger, agentId?}`; compaction happens inside `next(e)`. Skip `trigger: 'precompute'`. A hook's own awaits (not `$`/`next` calls) count against its 10 s budget.
- `prompt.context` `{blocks}`; return `{blocks: [...r.blocks, {name, text}]}` after `const r = await next(e)`. Sent once per conversation; re-read after compaction and `/clear`.
- `register(on, options)`: `options` holds `userConfig` values with defaults filled; changing one reloads the module.
- `$.ui.status(text|undefined)` (one per plugin), `$.ui.toast(text)`, `$.ui.log(text, {to: 'debug'})`, `$.ui.open({id, title, focus: true, closeOnEscape: true})`, `ui.render` `{component: 'Pane', requestId}`, elements via `$.ui.resolve(e)`: `Box, Text, Button, Select, Markdown, Code, Link`. `$.state` writes are refused inside `ui.render`; do them in command/handler code. `$.prompt.fill({text, mode: 'insert'})`.
- `$.fs.read/write/list/exists/stat`; `list` → `{name, kind, mtimeMs}[]`. `$.session.root()`, `$.session.cwd()`, `$.plugin.root`.
- `$.store.get/set/delete(key)`, persistent per plugin.
- Tests: `test(name, [{options}], async ($, on) => …)`. Nothing sits under the plugin: stub engine calls with `on('fs.read', (_, e) => ({value: …}))`, `on('model.fork', …)`, `on('agent.spawn', () => ({agentId, model}))`, `on('ui.toast', …)`; `mock.clock(on)` (`advance`, `settle`), `mock.store(on, entries?)`. Drive events with `$.session.start/end/compact`, `$.turn.complete`, `$.tool.call`, `$.tool.check`, `$.command.run`, `$.prompt.context`, `$.prompt.submit`, `$.ui.mount`.
- `claude plugin test <dir>`, `claude plugin validate <dir>`. The `.claude-plugin/types/` folder `tsconfig.json` extends is written by the engine when the plugin loads via `claude --plugin-dir plugins/workspace`; it is gitignored (`plugins/*/.claude-plugin/types/`).

## Review Focus

1. **Saver subagent killed by `/next:go`'s `/clear`.** The handoff trigger dispatches the saver, then `next` clears the session. Expect the save to finish anyway. Manual check in Task 9; if it is killed, the `next:go` hook must await the saver's `turn.complete` in a `turn.complete` hook before `next` clears (Task 6 test `handoff save dispatches before next:go proceeds` pins the dispatch order only).
2. **Concurrent triggers** (idle timer fires while a compaction save runs). Expect one save, not two: Task 5 test `a second save while one is in flight is skipped`.
3. **Stale marker after a crash.** A killed process leaves the `$.store` marker; the next launch must not attach. Task 2 test `session start drops a stale marker`.
4. **Path escape in the auto-allow.** `projects/p/../../repos/x/README.md`, `projects/p-other/a.md`, `projects/p/a.md.sh`. Expect normal permission handling for all. Task 5 test `auto-allow rejects paths outside the project or not markdown`.
5. **Project CLAUDE.md missing or malformed** (no frontmatter, no `## Progress`). Expect attach to work with `0/0` and empty fields, not a throw. Task 1 test `parses a doc with no frontmatter or progress`.

---

## File Structure

```text
plugins/workspace/
  .claude-plugin/plugin.json      + "types", "userConfig", version 0.4.0
  hooks/hooks.json                + "modules": ["./register.ts"]
  hooks/register.ts               wiring only: session.start registrations, delegates to units
  hooks/project.ts                pure: parse CLAUDE.md, set frontmatter field, extract lean rules
  hooks/files.ts                  $.fs: list project files newest-first, registered flag
  hooks/state.ts                  atoms + Api type alias
  hooks/attach.ts                 attach/detach/marker/restore, context block text
  hooks/save.ts                   detect/apply/announce pipeline, saver agent, auto-allow predicate
  hooks/ui.tsx                    status text, pane render, show command
  types/index.d.ts                PluginState for every atom
  tsconfig.json                   extends ./.claude-plugin/types/tsconfig.json
  tests/project.test.ts  tests/attach.test.ts  tests/save.test.ts
  tests/triggers.test.ts tests/ui.test.tsx     tests/engine.ts (shared stubs)
  skills/{new,resume,close,update}-project/SKILL.md   edits
  skills/auto-update/                                 deleted
  README.md, CLAUDE.md                                docs
.claude-plugin/marketplace.json                       version
```

---

### Task 1: Scaffold the module and pure project parsing

**Files:**

- Modify: `plugins/workspace/.claude-plugin/plugin.json`, `plugins/workspace/hooks/hooks.json`
- Create: `plugins/workspace/tsconfig.json`, `plugins/workspace/types/index.d.ts`, `plugins/workspace/hooks/register.ts`, `plugins/workspace/hooks/project.ts`, `plugins/workspace/hooks/state.ts`
- Test: `plugins/workspace/tests/project.test.ts`

**Interfaces:**

- Produces (`hooks/project.ts`, no `$`):
  - `type Frontmatter = Record<string, string>` (scalar `key: value` lines only; list values ignored)
  - `type RefFile = { path: string; description: string }` (first cell: `` `x` ``, `[t](x)` or `x` → `x`)
  - `type ProjectDoc = { frontmatter: Frontmatter; progress: { done: number; total: number }; refFiles: RefFile[] }`
  - `parseProjectDoc(text: string): ProjectDoc` — `progress` counts `- [x]`/`- [ ]` items under `## Progress` only, up to the next `## `.
  - `setFrontmatterField(text: string, key: string, value: string): string` — replace the line if present, else insert after `status:` (else before the closing `---`); text without frontmatter returned unchanged.
  - `leanIndexRules(skillText: string): string` — body of the `## Lean Index Rules` section up to the next `## `.
- Produces (`hooks/state.ts`):
  - `type Api = Parameters<Hook<'session.start'>>[0]` (use the d.ts's own name for `$` if one exists)
  - `type Attached = { name: string; dir: string }`
  - atoms: `attached: Attached | null`, `task: string`, `savedAt: number | null`, `turnsSinceSave: number`, `selected: string | null`, `preview: string`, `files: ProjectFile[]`, `marks: Record<string, 'new' | 'updated'>`, all `{plugin: 'workspace', key: <same name>}`. (`ProjectFile` from Task 4; declare it in `state.ts` now: `{ path: string; mtimeMs: number; isRegistered: boolean }`.)

- [ ] **Step 1: Write the failing tests** in `tests/project.test.ts`, against this repo's real project layout:

```ts
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
```

- [ ] **Step 2: Scaffold so the test runner loads the plugin**
  - `plugin.json`: add `"types": "./types/index.d.ts"` and `"userConfig": { "autoSave": {"type": "boolean", "title": "Auto-save project notes", "description": "Save findings and progress to the attached project automatically before compaction, before /next:go, when idle, and at session end.", "default": true}, "idleMinutes": {"type": "number", "title": "Idle save after (minutes)", "description": "Minutes without a new turn before an idle save. Chosen to fit the 1-hour prompt cache.", "default": 50} }`; version `0.4.0`.
  - `hooks/hooks.json`: add `"modules": ["./register.ts"]` beside the existing `SessionStart` entries.
  - `tsconfig.json`: same as `plugins/next/tsconfig.json`.
  - `types/index.d.ts`: `declare module 'claude-code' { interface PluginState { workspace: { … one field per atom in state.ts … } } }`, exporting `Attached` and `ProjectFile`.
  - `hooks/register.ts`: `export const register: Register = (on, options) => {}` (filled by later tasks).
  - Run `claude --plugin-dir plugins/workspace -p 'ok'` once so the engine writes `.claude-plugin/types/`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `claude plugin test plugins/workspace`
Expected: FAIL, `parseProjectDoc` not exported.

- [ ] **Step 4: Implement the three functions in `hooks/project.ts` and the atoms in `hooks/state.ts`**

Line-based parsing, same rules as `scripts/resume-project.py` (`parse_reference_files`, `extract_checklist`) except progress is scoped to `## Progress`. No YAML library.

- [ ] **Step 5: Verify**

Run: `claude plugin test plugins/workspace && claude plugin validate plugins/workspace && npx tsc -p plugins/workspace --noEmit`
Expected: 4 tests PASS; validate clean; tsc no errors.

- [ ] **Step 6: Commit**

```bash
git add plugins/workspace/{.claude-plugin/plugin.json,hooks,types,tsconfig.json,tests/project.test.ts}
git commit -m "feat(workspace): scaffold hooks module and project doc parsing"
```

---

### Task 2: Attachment, the clear marker, and the context block

**Files:**

- Create: `plugins/workspace/hooks/attach.ts`, `plugins/workspace/tests/engine.ts`, `plugins/workspace/tests/attach.test.ts`
- Modify: `plugins/workspace/hooks/register.ts`

**Interfaces:**

- Consumes: `parseProjectDoc`, atoms, `Attached`.
- Produces (`hooks/attach.ts`):
  - `MARKER = 'attached'`; store key `` `${MARKER}:${root}` `` with `root = await $.session.root()`; value `Attached`.
  - `attach($: Api, dir: string): Promise<{ok: true, name: string} | {ok: false, reason: string}>` — requires `<dir>/CLAUDE.md` to exist and `dir`'s parent directory to be named `projects`; sets `attached`, `task` (from `current-task:`), `savedAt: null`, `turnsSinceSave: 0`, `marks: {}`; calls `refreshStatus($)` (Task 3 provides it; stub to no-op until then).
  - `detach($: Api): Promise<void>` — resets every atom to its initial value, cancels the idle timer (Task 6 hook: export `onDetach: Array<() => void>` that `detach` calls), clears the status line.
  - `contextBlock(a: Attached, doc: ProjectDoc, task: string): {name: 'workspaceProject', text: string}` — lines: `Attached workspace project: <name> (<type>, <status>)`, `Path: <dir>`, `Branch: <branch>` / `Worktree: <worktree_path>` when present, `Current task: <task>` when set, then `Files:` and one `- <path> — <description>` per ref file. Ends with `Keep notes under <dir>. Call mcp__workspace__set_task when the focus of the work changes.`
- Produces (`tests/engine.ts`): `stubEngine(on, files: Record<string,string>): Seen` with `Seen = {toasts: string[]; writes: {path: string; text: string}[]; status: (string|undefined)[]; forks: string[]; spawns: string[]}`; stubs `fs.read/exists/stat/list/write`, `ui.toast/status/log/open`, `tool.register` (returns `{value: {tool: 'mcp__workspace__' + e.name}}`), `agent.register`, `command.register`, `session.start`, `session.root` (`'/ws'`), `session.cwd` (`'/ws'`). `fs.read` of any path ending `skills/update-project/SKILL.md` returns a minimal skill with a `## Lean Index Rules` section. Later tasks add `model.fork`/`agent.spawn` stubs per test.

Wiring in `register.ts`:
- `session.start`: register tools `attach` (`{dir: string}` "Attach a workspace project to this session. Call as the last step of /workspace:new-project and /workspace:resume-project with the project's absolute directory.") and `detach` (`{}`); then `$.store.delete` the marker for this root (fresh process never attaches).
- `tool.call` for each tool → `attach`/`detach`; `{deny: reason}` on failure.
- `tool.check` `{tool: /^mcp__workspace__/}` → `{decision: 'allow'}` (main loop only).
- `session.end`: if `reason === 'clear'` and attached → write marker; any other reason → delete marker. Then `detach`, then `return next(e)`.
- `prompt.submit` (main thread): if not attached and a marker exists → `attach($, marker.dir)`, delete the marker; on failure toast `workspace: could not restore <name> after /clear` once and stay detached.
- `prompt.context`: `const r = await next(e)`; if attached, append `contextBlock(...)` from a fresh `$.fs.read` of the CLAUDE.md. Wrap the read in try/catch → return `r`.

- [ ] **Step 1: Write the failing tests** in `tests/attach.test.ts`:
  - `attach sets state and rejects a non-project dir` — `attach('/ws/projects/demo')` → `{ok: true, name: 'demo'}`, `read($, attached)` equals `{name: 'demo', dir: '/ws/projects/demo'}`; `attach('/ws/src')` → `ok: false`.
  - `attachment survives clear and restores on the next prompt` — attach, `$.session.end({reason: 'clear', …})` → `attached` is `null`, store has `attached:/ws`; `$.prompt.submit({text: 'hi', …})` → `attached.name === 'demo'`, store key gone.
  - `resume and exit drop the attachment` — for `reason` in `['resume', 'prompt_input_exit', 'logout']`: after end, no marker, `attached === null`, and a following `prompt.submit` leaves it `null`.
  - `session start drops a stale marker` — `mock.store(on, {'attached:/ws': {name: 'demo', dir: '/ws/projects/demo'}})`, `$.session.start(…)`, `$.prompt.submit(…)` → `attached === null`.
  - `context block present only while attached` — `$.prompt.context({blocks: []})` has no `workspaceProject` block; after attach it has one whose text contains `Attached workspace project: demo (feature, active)` and `- design.md — Design`.
  - `context block falls through when the read throws` — attach, then make `fs.read` throw → `prompt.context` returns the engine's blocks unchanged.

- [ ] **Step 2: Run to verify they fail**

Run: `claude plugin test plugins/workspace`
Expected: the six new tests FAIL (`attach` not exported / tool not registered).

- [ ] **Step 3: Implement `hooks/attach.ts`, `tests/engine.ts`, and the wiring above**

- [ ] **Step 4: Verify**

Run: `claude plugin test plugins/workspace && claude plugin validate plugins/workspace`
Expected: all PASS; validate lists `prompt.context`, `prompt.submit`, `session.end`, `tool.check`, `tool.call`.

- [ ] **Step 5: Commit**

```bash
git add plugins/workspace/hooks plugins/workspace/tests
git commit -m "feat(workspace): attach projects to the session across /clear"
```

---

### Task 3: Current task tool and status line

**Files:**

- Create: `plugins/workspace/hooks/ui.tsx` (status part only)
- Modify: `plugins/workspace/hooks/register.ts`, `plugins/workspace/hooks/attach.ts`
- Test: `plugins/workspace/tests/ui.test.tsx`

**Interfaces:**

- Consumes: `setFrontmatterField`, `parseProjectDoc`, atoms.
- Produces:
  - `statusText(v: {name: string; done: number; total: number; savedAt: number | null; lastActive?: string; task: string; now: number}): string` → `⌂ <name> · <done>/<total> · saved <age>`; `<age>` from `savedAt`, else from `lastActive` (`YYYY-MM-DDTHH:MM`, local), formatted `now`/`<n>m`/`<n>h`/`<n>d`; omit the `saved` part when neither is known; append ` · <task>` truncated to 40 chars with `…` when `task` is set.
  - `refreshStatus($: Api): Promise<void>` — reads atoms + CLAUDE.md, calls `$.ui.status(statusText(...))`, or `$.ui.status(undefined)` when detached. A 60 s `$.clock.every` started on attach (cancelled via `onDetach`) keeps the age current.
  - `setTask($: Api, task: string): Promise<{ok: boolean; reason?: string}>` — trims, caps at 120 chars, sets `task`, writes `current-task:` into `<dir>/CLAUDE.md` via `setFrontmatterField`, refreshes status. Not attached → `{ok: false, reason: 'No workspace project is attached.'}`.
- Tool `set_task` (`{task: string}`), registered in `session.start`, description: "Record what is being worked on right now in the attached workspace project. Call whenever the focus of the work shifts; one short phrase."

- [ ] **Step 1: Write the failing tests** in `tests/ui.test.tsx`:
  - `status text formats counts, age and task` — `statusText({name:'demo', done:2, total:5, savedAt: now-4*60_000, task:'', now})` = `'⌂ demo · 2/5 · saved 4m'`; with `savedAt: null, lastActive: undefined` = `'⌂ demo · 2/5'`; with a 60-char task the result ends with `…` and the task part is 40 chars.
  - `status appears on attach and clears on detach` — `seen.status` last value starts `⌂ demo · 2/3` after attach, `undefined` after `mcp__workspace__detach`.
  - `set_task updates state and frontmatter` — call tool with `task: '  Review proposal  '` → `read($, task) === 'Review proposal'`, `seen.writes[0].text` contains `current-task: Review proposal\n`; status ends with `· Review proposal`.
  - `set_task without a project is denied` — result has `deny` and no write.

- [ ] **Step 2: Run to verify they fail** — `claude plugin test plugins/workspace`; Expected: 4 new FAIL.

- [ ] **Step 3: Implement** `statusText`, `refreshStatus`, `setTask`, the tool wiring; replace the Task 2 stub call.

- [ ] **Step 4: Verify** — `claude plugin test plugins/workspace`; Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/workspace/hooks plugins/workspace/tests
git commit -m "feat(workspace): current-task tool and project status line"
```

---

### Task 4: Project pane

**Files:**

- Create: `plugins/workspace/hooks/files.ts`
- Modify: `plugins/workspace/hooks/ui.tsx`, `plugins/workspace/hooks/register.ts`
- Test: `plugins/workspace/tests/ui.test.tsx`

**Interfaces:**

- Consumes: atoms (`files`, `selected`, `preview`, `marks`), `parseProjectDoc`, `RefFile`.
- Produces:
  - `listProjectFiles($: Api, dir: string, refFiles: RefFile[]): Promise<ProjectFile[]>` — recursive `$.fs.list`, skips names starting with `.`, max depth 3, kind `file` only, newest `mtimeMs` first; `isRegistered` true for `CLAUDE.md`, `README.md`, and any ref-file path.
  - `refreshFiles($: Api): Promise<void>` — fills `files`; if `selected` is null or gone, selects the first file and loads `preview`. Called on attach, on pane open, and after a save completes (Task 5).
  - `PANE = 'workspace:project'`; command `project` ("Show the attached workspace project") → if detached `{text: 'No workspace project is attached. Run /workspace:resume-project.'}`; else `refreshFiles`, `$.ui.open({id: PANE, title: <name>, focus: true, closeOnEscape: true})`, `{text: 'Project pane opened.'}`.
  - `atPath(fileAbs: string, cwd: string): string` — `@` + path relative to `cwd` when inside it, else absolute.
- Render (`ui.render` `{component: 'Pane', requestId: PANE}`), top to bottom: `Text` `<name> · <type> · <status>` (+ `branch`/`worktree_path`); `Text` progress bar of 20 cells (`█`/`░`) + ` <done>/<total>`; `Text` `Current task: <task>` (or dim `No current task`); `Select` key `files`, one option per file, label `<path>  <age>` + ` [unregistered]` when `!isRegistered` + ` [new]`/` [updated]` from `marks`; `onSelect` sets `selected`, reads the file into `preview`; `Markdown` of `preview` for `*.md`, `Code` otherwise; `Button` key `insert` hotkey `i` "Insert @path" → `$.prompt.fill({text: atPath(...) + ' ', mode: 'insert'})`; `Link` "Open in editor" to `file://<abs path>`.

- [ ] **Step 1: Write the failing tests** (mount with `$.ui.mount({plugin: 'workspace', surface: 'terminal', component: 'Pane', requestId: 'workspace:project', props: {...}})` as in the bundled `examples/pane.test.tsx`):
  - `files list newest first and flags unregistered` — stub `fs.list` for `/ws/projects/demo` with `design.md` (mtime 1), `scratch.md` (mtime 3), `CLAUDE.md` (mtime 2), `.gitignore` → `listProjectFiles` paths `['scratch.md', 'CLAUDE.md', 'design.md']`, `scratch.md` `isRegistered: false`.
  - `pane shows header, progress, current task and the selected file` — after attach + `set_task` + `$.command.run({command: 'project', …})`: finds Text `/demo · feature · active/`, Text `/2\/3/`, Text `/Current task: Review/`; a Markdown element whose text is `scratch.md`'s content.
  - `insert puts the @path in the prompt` — `ui.press({key: 'insert'})` → a `prompt.fill` stub saw `text: '@projects/demo/scratch.md '`.
  - `project command without attachment explains` — result text matches `/No workspace project is attached/`, no `ui.open` call.

- [ ] **Step 2: Run to verify they fail** — Expected: 4 new FAIL.

- [ ] **Step 3: Implement** `files.ts`, the command (registered in `session.start`), the render hook; call `refreshFiles` from `attach`.

- [ ] **Step 4: Verify** — `claude plugin test plugins/workspace`; Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/workspace/hooks plugins/workspace/tests
git commit -m "feat(workspace): project pane with file browser and @path insert"
```

---

### Task 5: Save pipeline (detect → apply → announce) and the scoped auto-allow

**Files:**

- Create: `plugins/workspace/hooks/save.ts`, `plugins/workspace/tests/save.test.ts`
- Modify: `plugins/workspace/hooks/register.ts`

**Interfaces:**

- Consumes: atoms, `parseProjectDoc`, `leanIndexRules`, `refreshStatus`, `refreshFiles`.
- Produces (`hooks/save.ts`):
  - `type Trigger = 'compact' | 'handoff' | 'idle' | 'end' | 'manual'`
  - `type SaveOutcome = {kind: 'skipped'; why: 'detached' | 'no-new-turns' | 'disabled' | 'in-flight'} | {kind: 'nothing'} | {kind: 'dispatched'; items: string[]; agentId: string} | {kind: 'failed'; error: string}`
  - `startSave($: Api, trigger: Trigger, opts: {autoSave: boolean}): Promise<SaveOutcome>` — gates in order: detached → `detached`; `trigger !== 'manual' && !opts.autoSave` → `disabled`; in-flight → `in-flight`; `trigger !== 'manual' && turnsSinceSave === 0` → `no-new-turns`. Then detect, then spawn. On dispatch: `turnsSinceSave = 0`, append items to a module-level `alreadySaved: string[]` (cleared on detach). Resolves at dispatch, not at completion.
  - `detectPrompt(doc: ProjectDoc, claudeMd: string, alreadySaved: string[]): string` — asks: "What findings, decisions or progress from this conversation are not yet in the project files below? Answer exactly `NOTHING`, or one line per item: `- <target file>: <item>`." Includes the project CLAUDE.md text (it holds the Reference Files table and progress) and an `Already saved this session:` list.
  - `parseDetected(text: string): string[] | null` — `null` for `NOTHING` (trimmed, case-insensitive) or no `- ` lines; else the `- ` lines without the dash.
  - `applyPrompt(a: Attached, items: string[], consolidate: string): string` — project dir, the items, "Edit only `*.md` files under `<dir>`. Never change `status:`. Update `last-active:` to now. If CLAUDE.md would exceed 100 lines, run exactly `python3 "<consolidate>" <name>` first. End with one line: `updated: <summary>`, `nothing`, or `failed: <error>`."
  - `isAllowedForSaver(a: Attached, tool: string, input: unknown, consolidate: string): boolean` — `Edit`/`Write`: `input.file_path` normalized (resolve `.`/`..` segments) starts with `<dir>/` and ends with `.md`. `Bash`: `input.command` equals `python3 "<consolidate>" <name>` exactly. Anything else false.
  - `consolidate = <$.plugin.root>/scripts/consolidate-project.py`.
- Wiring in `register.ts`:
  - `session.start`: `$.agent.register({name: 'saver', description: 'Saves workspace project notes (internal)', prompt, tools: ['Read', 'Edit', 'Write', 'Bash'], maxTurns: 12, omitClaudeMd: true})`, where `prompt` is a static role line plus `leanIndexRules()` of `<$.plugin.root>/skills/update-project/SKILL.md`. If that read fails, the prompt instead tells the saver to Read that file's `## Lean Index Rules` section first.
  - `agent.offer` `{agent: 'workspace:saver'}` → `{isOffered: false}`.
  - Tool `save` (no args; description: "Save findings, decisions and progress from this conversation to the attached workspace project now.") → `startSave($, 'manual', …)` → result text per outcome: `Saving N items to <name> in the background; a toast reports the result.` / `Nothing new to save.` / `No workspace project is attached.` / `Save failed: <error>`.
  - `tool.check` with `e.agentId` in the saver set: `isAllowedForSaver` → `{decision: 'allow'}`, else `next(e)`; any throw → `next(e)`.
  - `turn.complete` with `e.agentId` in the saver set: parse the answer's last line; `updated: X` → toast `workspace: saved — X`, set `savedAt = now`, mark touched files `updated`/`new` if the summary names them (best effort, by filename match against `files`), `refreshFiles`, `refreshStatus`; `nothing` → no toast; `failed: E` or other → toast `workspace: save failed — E`. Clear in-flight. No retry.
  - After each `model.fork` with `usage`, log at debug level (`$.ui.log(text, {to: 'debug'})`) the line `workspace: detect trigger=<t> cache_read=<n> input=<m>`.

- [ ] **Step 1: Write the failing tests** in `tests/save.test.ts` (stub `model.fork` and `agent.spawn` per test):
  - `nothing detected means no spawn, toast, or write` — fork text `NOTHING` → outcome `{kind: 'nothing'}`, `seen.spawns`, `seen.toasts`, `seen.writes` empty.
  - `detected items dispatch the saver and toast its report` — fork text `- design.md: chose prompt.context`; outcome `dispatched` with that item; `$.turn.complete({agentId: 'a1', answer: '...\nupdated: 1 finding to design.md', …})` → toast `workspace: saved — 1 finding to design.md`; `savedAt` set.
  - `saver failure toasts once` — answer `failed: disk full` → one toast matching `/save failed — disk full/`.
  - `no new turns skips automatic triggers but not manual` — attach, `startSave('idle')` → `no-new-turns`, no fork; `startSave('manual')` → fork called.
  - `a second save while one is in flight is skipped` — two `startSave` before the saver completes → second is `in-flight`.
  - `already saved items are passed to the next detect` — after one dispatch, the next fork prompt contains `Already saved this session:` and the first item.
  - `auto-allow covers markdown under the project only` — `$.tool.check({tool: 'Edit', input: {file_path: '/ws/projects/demo/design.md'}, agentId: 'a1'})` → `allow`; consolidate Bash exact command → `allow`.
  - `auto-allow rejects paths outside the project or not markdown` — `/ws/projects/demo/../../repos/x/README.md`, `/ws/projects/demo-other/a.md`, `/ws/projects/demo/a.md.sh`, `Bash` `rm -rf /ws` → each falls through to the stub engine's verdict (`ask`); same Edit from the main loop (no `agentId`) → `ask`.
  - `auto-allow falls through when its own code throws` — `input: null` → `ask`.

- [ ] **Step 2: Run to verify they fail** — Expected: 9 new FAIL.

- [ ] **Step 3: Implement** `save.ts` and the wiring.

- [ ] **Step 4: Verify** — `claude plugin test plugins/workspace`; Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/workspace/hooks plugins/workspace/tests
git commit -m "feat(workspace): fork-detect and subagent-apply save pipeline"
```

---

### Task 6: Save triggers and the autoSave setting

**Files:**

- Create: `plugins/workspace/tests/triggers.test.ts`
- Modify: `plugins/workspace/hooks/register.ts`

**Interfaces:**

- Consumes: `startSave`, `onDetach`, `options.autoSave`, `options.idleMinutes`.
- Produces (all main-thread only, `e.agentId === undefined`; every body in try/catch that falls through):
  - `turn.complete`: `turnsSinceSave += 1`; cancel and re-arm `idleTimer = $.clock.after(idleMinutes * 60_000, () => void startSave($, 'idle', opts))`. Push `() => idleTimer?.cancel()` onto `onDetach` once.
  - `session.compact`: if attached and `e.trigger !== 'precompute'`: `await startSave($, 'compact', opts)`, then `return next(e)`.
  - `command.run` `{command: 'next:go'}`: `await startSave($, 'handoff', opts)`, then `return next(e)`.
  - `session.end`: before the marker/detach logic, `void startSave($, 'end', opts)` (never awaited).

- [ ] **Step 1: Write the failing tests** in `tests/triggers.test.ts` (attach first; one main `turn.complete` unless stated):
  - `compaction waits for detection before proceeding` — record order in `model.fork` and `session.compact` engine stubs → `['fork', 'compact']`.
  - `precompute compaction does not save` — `trigger: 'precompute'` → no fork.
  - `handoff save dispatches before next:go proceeds` — order `['fork', 'spawn', 'command:next:go']`.
  - `idle fires after the threshold and only after a new turn` — `test(…, {options: {idleMinutes: 50}})`; `clock.advance(49 * 60_000)` → no fork; `advance(60_000)` → one fork; `advance(50 * 60_000)` with no new turn → still one.
  - `autoSave false suppresses every trigger` — `options: {autoSave: false}`: compact, `next:go`, idle and end produce no fork; the `save` tool still forks.
  - `detach cancels the idle timer` — turn, detach, advance 60 min → no fork.
  - `compaction proceeds when the save throws` — `model.fork` stub throws → `session.compact` still reaches the engine stub.
  - `session end starts a save without waiting` — `model.fork` stub never resolves; `$.session.end({reason: 'clear', …})` resolves; marker written.

- [ ] **Step 2: Run to verify they fail** — Expected: 8 new FAIL.

- [ ] **Step 3: Implement** the four hooks.

- [ ] **Step 4: Verify** — `claude plugin test plugins/workspace && claude plugin validate plugins/workspace && npx tsc -p plugins/workspace --noEmit`; Expected: all PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add plugins/workspace/hooks plugins/workspace/tests
git commit -m "feat(workspace): auto-save on compaction, /next:go, idle and session end"
```

---

### Task 7: Skills — attach, detach, current task, manual save; remove auto-update

**Files:**

- Modify: `plugins/workspace/skills/new-project/SKILL.md`, `skills/resume-project/SKILL.md`, `skills/close-project/SKILL.md`, `skills/update-project/SKILL.md`
- Delete: `plugins/workspace/skills/auto-update/`

Changes (copy is final; keep each skill's voice):

- **new-project**, end of Step 4: "If the `mcp__workspace__attach` tool is available, call it with `dir` = the absolute project directory. If it is not available (Claude Code without plugin hooks), skip this."
- **resume-project**:
  - New Step 2.1 after Step 2: same attach instruction with `dir` = `P.dir`.
  - Step 3 summary template: add `**Current task:** <P.frontmatter["current-task"]>` under the project header when set.
  - Step 4: after the task is chosen, "call `mcp__workspace__set_task` with the chosen task when the tool is available."
  - Delete Step 6.
- **close-project**, Step 2.5f: after the handoff marker cleanup, "If `mcp__workspace__detach` is available, call it."
- **update-project**: replace Step 1 and "After the Fork Returns" with:
  - Step 1: "If the `mcp__workspace__save` tool is available, call it and relay its result text to the user; stop. Otherwise apply Steps 2-4 yourself in this session (no fork)." Steps 2-4 lose the "(fork)" labels, the fork directive, and the `--auto`/`$ARGUMENTS` stripping note; report the outcome directly.
  - Delete all `--auto`, CronCreate/CronList/CronDelete, and loop-driven text.
  - Keep `## Lean Index Rules` verbatim (the mod reads it).

- [ ] **Step 1: Make the edits and delete `skills/auto-update/`**
- [ ] **Step 2: Verify the mod still finds the rules** — `grep -c '^## Lean Index Rules' plugins/workspace/skills/update-project/SKILL.md`; Expected: `1`.
- [ ] **Step 3: Verify no stale references** — `grep -rn "auto-update\|--auto\|Cron" plugins/workspace/skills`; Expected: no output.
- [ ] **Step 4: Run the Python suites** — `cd plugins/workspace && for t in tests/test_*.py; do python3 "$t" || break; done; bash tests/test_setup.sh`; Expected: all OK.
- [ ] **Step 5: Commit**

```bash
git add -A plugins/workspace/skills
git commit -m "feat(workspace): skills attach, detach and save through the mod; drop auto-update"
```

---

### Task 8: Docs and version

**Files:**

- Modify: `plugins/workspace/README.md`, `plugins/workspace/CLAUDE.md`, `.claude-plugin/marketplace.json`

- [ ] **Step 1: Edit**
  - README: remove the `/workspace:auto-update` row; add a short "Session UI and auto-save" section: status line, `/project` pane, auto-save triggers, the two `userConfig` options (`/config`), and that it needs Claude Code with plugin hooks (degrades to skills only).
  - CLAUDE.md: Layout gains `hooks/register.ts` + `hooks/*.ts`, `types/`, `tests/*.test.ts(x)`; skills table loses `auto-update` (count 10 → 9); Dev Loop gains `claude plugin test .` and `npx tsc -p . --noEmit`.
  - marketplace.json: `workspace` version `0.4.0`.
- [ ] **Step 2: Verify** — `npx markdownlint-cli2 'plugins/workspace/**/*.md' 'docs/superpowers/**/*.md'` and `claude plugin validate plugins/workspace --strict`; Expected: 0 errors; clean.
- [ ] **Step 3: Commit**

```bash
git add plugins/workspace/README.md plugins/workspace/CLAUDE.md .claude-plugin/marketplace.json
git commit -m "docs(workspace): document session UI and auto-save; bump to 0.4.0"
```

---

### Task 9: Manual verification in a live session

No code unless a check fails (then fix in the owning task's files, with a test).

- [ ] **Step 1:** `WORKSPACE_ROOT=$PWD claude --plugin-dir plugins/workspace --plugin-dir plugins/next` from the main checkout.
- [ ] **Step 2:** `/workspace:resume-project workspace-mod-native` → status line `⌂ workspace-mod-native · …`; `/project` opens the pane; select a file; press `i` → `@…` in the composer; Esc closes.
- [ ] **Step 3:** Discuss a decision for a few turns; `/compact` → toast `workspace: saved — …`; the finding is in a detail file; CLAUDE.md ≤ 100 lines; no permission prompt appeared.
- [ ] **Step 4:** `/clear`, then ask "which project are we on?" → Claude answers from the injected block; status line back.
- [ ] **Step 5:** `/next:go` → save toast before the clear; after resume, project still attached; the save's edits are on disk (Review Focus 1: if the saver was killed by the clear, fix per that item).
- [ ] **Step 6:** Restart `claude` → no status line, no attachment.
- [ ] **Step 7 (cache TTL):** leave a session idle with `idleMinutes` set to 50; check the debug log for `cache_read=`. Non-zero settles the 1-hour TTL; zero means revisit the default. Record the result in `projects/workspace-mod-native/design.md`.
