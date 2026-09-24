---
name: update-project
description: Update project documentation from what was accomplished in this session
argument-hint: [name-or-number]
---

# Update Project Documentation

Update a project's documentation based on what was accomplished in the
current conversation. Apply edits directly.

## Scope Rules

**Update:** files under `projects/<name>/` in the workspace — CLAUDE.md
(index, checklists, progress) and detail files (investigation notes, test
results, plans, etc.).

**NEVER touch during this command:**
- The `status:` frontmatter field — use `/workspace:close-project` to change it
- Memory files (`memory/MEMORY.md`, `memory/project_*.md`)
- Internal session tasks (TaskCreate / TaskUpdate)
- Repo source files under `repos/`

## Lean Index Rules

CLAUDE.md is an index, not a document — it orients on what the project is
and where to look. All detailed content belongs in a detail file listed in
the `## Reference Files` table. These rules bind every edit this command
makes, including the ones the dispatched background agent applies:

- **One line per milestone.** Progress entries are one line per milestone —
  no `#`/`##` headings, no test counts, no review narrative.
- **Replace, don't append.** When a milestone completes, delete the
  in-progress line(s) it supersedes instead of adding a new line alongside
  them. A milestone appears in `## Progress` exactly once, in its final
  state.
- **Narrative goes to a detail file, never into CLAUDE.md directly.**
  Findings, test output, investigation notes, and review discussion go into
  a detail file already listed in Reference Files (add a new row if you
  create one).
- **Hard cap: CLAUDE.md must not exceed 100 lines.** Before writing, count
  the current file's lines. If it already exceeds 100, or the update would
  push it over, run consolidation first (Step 4), then apply the update to
  the consolidated file. If consolidation reports `over_threshold_no_sections`,
  manually move content into a detail file first. If it would still exceed
  the cap afterward, move the overflow into a detail file and leave only an
  index-appropriate pointer line in CLAUDE.md.

## Step 1: Resolve Project

Use the project already loaded in this conversation (from
`/workspace:resume-project` or any earlier project interaction). If `$ARGUMENTS`
has a token, use that as the project name instead.

If no project is in context and no argument was given, ask which project.

## Step 2: Read Current State

Read `projects/<name>/CLAUDE.md` in full.

## Step 3: Identify Updates

Review the conversation history and identify:

1. **Checklist items completed** — `- [ ]` items now done.
2. **New checklist items** — work discovered or queued.
3. **Detail file updates** — new findings, test results, or analysis to
   add to existing detail files, or new detail files to create. Narrative
   never goes into CLAUDE.md directly (see Lean Index Rules).
4. **New detail files in Reference Files table** — files created in
   `projects/<name>/` not yet registered in CLAUDE.md's table.
5. **Progress entries** — milestones or outcomes to record, one line each.
   Replace the in-progress line a completed milestone supersedes rather
   than appending (see Lean Index Rules).
6. **`last-active` timestamp** — always update `last-active: <YYYY-MM-DDTHH:MM>`
   in the frontmatter to the current date and time when any other update is
   applied. If the field does not exist yet, add it after the `status:` line.

If nothing to update, check CronList for any job whose prompt contains
`update-project`. If one exists, cancel it with CronDelete. If CronDelete
succeeds, tell the user:

> Nothing to update — auto-update stopped. Run
> `/workspace:auto-update` to re-enable.
>
> Cache is likely cold by now — consider `/clear` then
> `/workspace:resume-project` to start a fresh session at lower cost.

If CronDelete fails, warn: "Tried to stop auto-update but CronDelete
failed — the loop may still be running. Use CronList to check."

If no cron job exists (manual invocation), just say "Nothing to update."
Either way, stop.

## Step 4: Dispatch to Background Agent

Resolve the absolute path to the consolidation script before building the
prompt: `${CLAUDE_PLUGIN_ROOT}/scripts/consolidate-project.py`.

Build a self-contained agent prompt from the updates identified in
Step 3:

> Update project documentation for project `<name>`.
>
> **Project directory:** `<absolute path to projects/<name>/>`
> **Consolidation script:** `<absolute path to consolidate-project.py>`
>
> Read `CLAUDE.md` in the project directory and count its lines. Then
> apply these updates:
> - [specific checklist items to check off]
> - [specific new items to add]
> - [specific detail files to update, with the content to add]
> - [new Reference Files table rows if any]
> - [progress entries to record under the Progress section]
>
> **Lean Index Rules (binding on every edit):**
> - Progress entries are one line per milestone, no hashes, no test counts, no review narrative.
> - Replace, don't append: when a milestone completes, delete the in-progress line(s) it supersedes instead of adding a new line.
> - Narrative (findings, test output, investigation notes, review discussion) goes into a detail file already listed in Reference Files (add a new row if you create one) — never into CLAUDE.md directly.
> - Hard cap: CLAUDE.md must not exceed 100 lines. Before writing,
>   check whether it already exceeds 100 lines, or whether your edit would
>   push it past 100. If either is true, run
>   `python3 "<consolidation script path>" <name>`, then re-read the
>   consolidated CLAUDE.md and apply your updates to that file instead.
>   If the script returns `over_threshold_no_sections`, manually move
>   content into a detail file before applying the update. If the update
>   would still exceed 100 lines after consolidation, move the overflow
>   into a detail file (add a Reference Files row) and leave only a
>   pointer line in CLAUDE.md.
>
> Also update the `last-active` frontmatter field to the current date
> and time (YYYY-MM-DDTHH:MM) — this drives SessionStart project ordering.
>
> Rules: only edit files under the project directory. Never change the
> `status:` frontmatter field. Use the Edit tool for existing files,
> Write tool for new files. Edit each file individually — do not rewrite
> entire files.
>
> When done, count CLAUDE.md's lines again and report both counts
> alongside your summary.

Dispatch using the Agent tool with `run_in_background: true`. Say
"Updating project docs in the background." and return immediately.

**On agent completion notification:** Check the agent's result. If it
succeeded, briefly confirm what was updated, including the line-count
change the agent reported (e.g. "CLAUDE.md: 62 → 71 lines"). If the
session produced durable domain-level knowledge (not just project status),
suggest `/workspace:update-domain`.

**On agent failure:** Tell the user: "Background update failed:
[error summary]. Run `/workspace:update-project` manually to retry."
Do NOT cancel the cron on agent failure — the next invocation may
succeed if the failure was transient.

