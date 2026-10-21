# Workspace Plugin: Mod-Native Rework

Date: 2026-10-21
Plugin: `plugins/workspace/` (currently 0.3.0)
Project notes: `projects/workspace-mod-native/`

## Intent

The workspace plugin tracks work in `projects/<name>/` folders, but nothing in
the Claude Code UI shows that a project is loaded, what is in it, or what is
being worked on. The user loses track of which docs exist and opens the folder
in a separate editor to find a file to `@`-reference. Keeping notes current
depends on manually running `/workspace:update-project`, or on the
`/workspace:auto-update` loop: a self-re-arming one-shot cron, a `--auto`
sentinel, a background fork whose report arrives on a later turn, and
stale-context workarounds. All of it is prompt engineering that stands in for
hooks Claude Code did not have.

Mods (plugin function hooks) now provide those hooks natively: turn and idle
detection, a cache-served side question over the session transcript,
pre-compaction and session-end hooks, panes with rendered markdown, status
line entries, and model-callable tools. This rework moves the workspace
plugin's *session-time* behavior onto a mod.

## Goals

1. **Always know which project is active.** A status line entry is present
   whenever a project is attached, absent otherwise.
2. **Browse the project without leaving the terminal.** An on-demand pane
   lists the project's files and renders the selected one as markdown, with a
   one-press way to insert its `@path` into the prompt.
3. **See what is being worked on now.** The pane shows the current task,
   kept current by Claude as the work shifts.
4. **Notes stay current without manual commands.** Findings, decisions and
   progress from the conversation are saved automatically at the moments
   context is at risk.
5. **Claude always knows the project too.** While attached, every prompt
   carries a short project section, so a fresh context (including after a
   manual `/clear`) is not blind to the project the mod still shows.

## Non-goals

- Reworking `setup-environment`, `create-domain`, `update-domain`,
  `close-project` or `consolidate-project`. They stay prompt-driven skills.
- Replacing `new-project` or `resume-project` with mod code. They stay skills
  and gain one step each (see Attachment).
- Changing the project folder layout, frontmatter schema (beyond one new
  field), or the lean-index rules.
- The workspace plugin's own `handoff` skill and its `SessionStart`
  `handoff.py read` hook. Untouched in this round.
- Recall-by-shape search ("do we have a doc about X?"). The user covers this
  with `/btw`.
- Multi-repo awareness (repo map, remotes, worktree discovery, git
  guardrails). A separate proposed change to this plugin covers it; keep the
  status line and pane code easy to extend for it.

## Anti-goals (failure even if it technically works)

- **Silent auto-attach.** A fresh launch must never start auto-saving into a
  project the user did not resume in that session.
- **Noise.** Auto-save must not toast, write, or spend a model call when
  nothing changed since the last save.
- **Bloating CLAUDE.md.** Auto-save must honor the existing lean-index rules
  (one line per milestone, replace don't append, narrative to detail files,
  100-line hard cap) exactly as `/workspace:update-project` does today.
- **Hard dependency on `next`.** Both plugins must work alone.

## Decisions (made with the user)

| Topic | Decision |
|-------|----------|
| Auto-save approval | Write without asking; announce with a one-line toast. |
| Auto-save default | On by default whenever a project is attached. No opt-in command. |
| Idle threshold | 50 minutes, chosen to fit the 1-hour prompt cache. Configurable (see below). |
| `/workspace:auto-update` | Removed; superseded. |
| `/workspace:update-project` | Kept as the manual "save now". |
| Project visibility | Status line always; project pane on demand (opened by command/keybinding, closed by Esc). |
| Pane contents | Header, progress bar, **current task directly below the progress bar**, file list, rendered preview of selected file, insert-`@path` and open-in-editor actions. |
| Current task | What is actually being worked on now in the session, not the first unchecked plan item. |
| Auto-attach on launch | Never. Moving to a fresh context while keeping the project is what `/next:go` is for. |
| `next` integration | Small, optional text changes to `next:go` are acceptable; the plugins are independent but used together. |

## Design

### Attachment (which project is active)

- `/workspace:new-project` and `/workspace:resume-project` each end by
  attaching the project to the mod. Builder chooses the mechanism; options
  include a mod-registered model tool (e.g. `workspace_attach`) called as the
  skill's last step, or the mod observing the skill's existing
  `resume-project.py` Bash call via a `tool.call` hook (the pattern `next`
  uses for `handoff.py arm`).
- The mod keeps the attached project in `$.state` for the session. Detaching
  happens on `/workspace:close-project` and when a different project is
  attached.
- **Across `/clear`:** if a project is attached when the session is cleared
  (by `/next:go` or a manual `/clear`), the fresh context stays attached.
  The engine fires **no `session.start` after a `/clear`** (the process
  continues under a new session id; see `SessionEndInput`). So: at
  `session.end` with reason `clear`, write the attachment to `$.store` keyed
  by the launch directory; restore it lazily on the first main-thread
  `turn.start` (or `prompt.submit`) after the clear, then delete the marker.
  Do not rely on `$.state` surviving the session-id change. *(Claude's call:
  a manual `/clear` keeps the project too, since it is the same window.)*
- **Every other end reason detaches** (`resume`, `logout`, exit, etc.):
  clear `$.state` and any `$.store` marker, so an in-session `/resume` into
  another conversation never inherits the project. *(Claude's call, from
  the no-silent-auto-attach anti-goal.)*
- **Fresh launch** (startup, `--resume`, `--continue`): never attaches.
  The existing `recent-projects.py` SessionStart listing remains the hint.

### Context injection

While attached, a `prompt.compose` hook appends one session-scoped section:
project name, path, type/status, branch or worktree when present, current
task, and the Reference Files table as one line per file. Terse by design;
detail stays in the files and the pane. Rebuilt when attachment, current
task, or the file set changes, not on every turn, so the cached prompt
prefix stays stable between those events. No section when detached.

This closes the gap where the mod restores attachment after a `/clear` but
the model's context holds nothing about the project. It also makes the
`next:go` note's project line unnecessary (see `next` integration).

### Status line

While attached: `⌂ <project> · <done>/<total> · saved <age>` where
`<done>/<total>` counts the `## Progress` checklist and `<age>` is time since
the last auto-save (or `last-active` if none this session). The current task
may be appended, truncated, if space allows (builder's call). Cleared on
detach.

### Project pane

Opened on demand by a mod command (name is the builder's call, e.g.
`/workspace:show`); closed by Esc. No custom keybinding: the mod API cannot
register new keys. Contents, top
to bottom:

1. Project name, type, status; branch or worktree path when present.
2. Progress bar from the `## Progress` checklist.
3. **Current task** (see below).
4. File list: every file under `projects/<name>/`, newest first, including
   files *not* in the Reference Files table (these are the ones that get
   lost today; mark them visibly). Files auto-save just wrote or changed get
   a "new"/"updated" marker for the session.
5. Selected file rendered with the `Markdown` element. Non-markdown files may
   show as `Code` or plain text (builder's call).
6. Actions on the selected file: insert `@<path>` into the prompt
   (`$.prompt.fill` or equivalent), open in editor (a `file:` link).

The pane and status line refresh when auto-save writes, when the current task
changes, and when the pane opens. Live file watching is not required.

`scripts/resume-project.py` already implements frontmatter parsing,
checklist extraction, project file listing and unregistered-file detection.
The builder may call it via `$.process.run` or reimplement the needed parts
over `$.fs`; either is acceptable.

### Current task

- The mod registers a model-callable tool (e.g. `workspace_set_task`, one
  short string). Its description tells Claude to call it whenever the focus
  of the work shifts. *(Claude's call: a tool call is near-free and
  immediate; inferring the task on a timer would be stale or expensive.)*
- The value is written to a new `current-task:` field in the project
  CLAUDE.md frontmatter, so `resume-project` and `next:go` can show it in the
  next session, and held in `$.state` for drawing.
- `resume-project` presents `current-task` in its summary when set.

### Auto-save

Replaces the `auto-update` cron loop and the fork-dispatch machinery inside
`update-project`.

**Triggers** (only while a project is attached, and only if at least one
main-thread turn completed since the last save):

1. `session.compact`: before compaction proceeds (await the save, then
   `next(e)`).
2. **`/next:go`:** the workspace mod hooks `command.run` for `next:go` and
   runs a full save *before* calling `next(e)`, so the handoff happens with
   notes current. This needs no change to `next` and is inert when `next`
   is not installed. (After the clear, `$.model.fork` returns
   `nothing-to-fork`, so the save must happen before.)
3. `session.end` (exit, manual `/clear`): **best-effort only**, within the
   chain's short wall-clock budget (`next.budget`, `next.signal`). A reduced
   or skipped save is acceptable; it must never block exit. The user
   explicitly accepted that a manual `/clear` does not get a thorough save.
4. Idle: no new turn for the configured threshold (default 50 minutes),
   timed by the mod with `$.clock` from each main-thread `turn.complete`.

**Pipeline** *(Claude's recommendation; the builder may adjust)*:

1. **Detect** with `$.model.fork`: one tool-less question over the session
   transcript, served from the prompt cache: "What findings, decisions or
   progress since the last save are not yet in the project files?"
   Include the project's current Reference Files table and progress list in
   the prompt so the answer can target files. Answer "nothing" means stop
   silently.
2. **Apply** with a subagent (`$.agent.spawn`) given the detected items, the
   project path, and the lean-index rules verbatim from today's
   `update-project` skill. It edits CLAUDE.md (progress, `last-active`) and
   detail files (findings go to the related detail file; a finding with no
   home creates a new detail file and a Reference Files row). Never touches
   `status:`, memory files, or repo sources.

   **Permissions (decided with the user):** the save must not stall on
   permission prompts while the user is away. The mod auto-allows the
   subagent's `Edit`/`Write` calls (via a `tool.check` hook scoped to that
   subagent's `agentId`) **only for markdown documents under
   `projects/<name>/`** (`*.md`). Any other path or file type, and every
   other tool that would prompt, goes through normal permission handling.
   Do not use a blanket `acceptEdits` permission mode.
3. **Announce** with `$.ui.toast`, e.g.
   `workspace: saved 2 findings to design.md, checked 1 progress item`.
   On failure, toast the failure; do not retry in a loop.

The "since last save" boundary must be tracked so repeated saves do not
re-extract the same items (e.g. a turn count or message uuid in `$.state`).

**Manual save:** `/workspace:update-project` runs the same pipeline
immediately, ignoring the "new turn" gate, and reports the outcome in the
transcript. Whether it stays a skill that calls into the mod or becomes a mod
command is the builder's call. Its `--auto` handling and all cron steps are
removed.

**Configuration:** expose the idle threshold (minutes) as a plugin
`userConfig` option, default 50, and an `autoSave` boolean option, default
on. With `autoSave` off, no trigger fires; `/workspace:update-project`
still works as the manual save.

**Cache verification:** the 50-minute default assumes the 1-hour prompt
cache (the user's understanding). It was not verified in-session: a Claude
Code tool description claimed the default 5-minute TTL, and no override
was found in the user's settings or environment. The mod should log
`usage.cache_read_input_tokens` from each idle `$.model.fork` (debug log or
`$.telemetry`) so the first real idle save shows which TTL applies. If it
reads zero, revisit the default.

### `next` integration

None required. Attachment across the clear, the pre-handoff save, and
telling the fresh context which project and task are active (Context
injection) are all handled by the workspace mod, not by `next`.

### Removals

- `skills/auto-update/` (whole skill).
- `update-project`: Step 1 fork dispatch, `--auto` sentinel, "After the Fork
  Returns" contract, all CronCreate/CronList/CronDelete steps.
- `resume-project` Step 6 (suggest auto-update loop).
- README and CLAUDE.md references to the above.

## Constraints

- Mods have no Node or DOM; everything external goes through `$` (`$.fs`,
  `$.process`, `$.clock`, `$.model`, `$.agent`, `$.ui`).
- Python helpers stay stdlib-only, `python3` on PATH (existing plugin rule).
- The plugin keeps working on Claude Code versions without function hooks in
  degraded form: skills still work, the UI and auto-save are absent.
  `next` documents the same posture.
- Follow the `next` plugin as the in-repo reference for layout:
  `hooks/hooks.json` with `modules`, `hooks/register.ts(x)`,
  `types/index.d.ts` declaring `PluginState`, `tests/*.test.ts` run by
  `claude plugin test`, and `claude plugin validate` clean.
- Version bump in `plugin.json` and the marketplace manifest (minor:
  0.3.0 → 0.4.0, since a skill is removed and behavior changes by default).

## Error handling

The mod fails open everywhere: an error in its own code never blocks a
turn, a tool call, compaction, a `/clear`, or exit.

- `tool.check` auto-allow: on error, fall through to normal permission
  handling (`next(e)`); never deny.
- `prompt.compose`: on error, send the prompt without the project section.
- Attachment restore after `/clear`: on error, stay detached and toast once.
- Auto-save: on error, toast the failure and continue the chain; no retry
  loop.
- Pane and status line: on render error, the engine's default fallback is
  acceptable.

## Left to the builder

- Mod module structure, state shape, and file split.
- Exact command and tool names.
- Whether the pane reads project data via `resume-project.py` or `$.fs`.
- How attachment is signaled from the skills.
- The `session.end` save strategy within its time budget.
- Pane layout details, markers, and narrow-terminal behavior.
- Test design (mod tests with `claude plugin test`; existing Python tests
  stay green).

## Testing expectations

- `claude plugin validate plugins/workspace` clean; `tsc` clean.
- Mod tests cover: attach/detach, attachment surviving a `clear` (restored
  on the first turn after) and dropped on `resume`/startup, save running
  before `next:go` proceeds, the edit auto-allow covering only `*.md` under
  the project folder, idle timer firing only after a
  new turn, "nothing" producing no toast or write, toast on a save,
  current-task tool updating state and frontmatter, the `prompt.compose`
  section present only while attached, `autoSave: false` suppressing every
  trigger, and each hook falling through when its own code throws.
- Existing `plugins/workspace/tests/` Python suites pass.
- Manual check: resume a project, confirm the status line; open the pane,
  insert an `@path`; have a multi-turn discussion, `/compact`, confirm a
  toast and the finding in a detail file; `/next:go`, confirm the project is
  still attached in the fresh context; manual `/clear`, ask "which project
  are we on?" and confirm Claude answers from the injected section.
