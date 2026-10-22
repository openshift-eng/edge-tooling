---
name: update-project
description: Update project documentation from what was accomplished in this session
argument-hint: [name-or-number]
---

# Update Project Documentation

Update a project's documentation based on what was accomplished in the
current conversation.

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
makes:

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

## Step 1: Save

If the `mcp__workspace__save` tool is available, call it and relay its
result text to the user; stop. Otherwise apply Steps 2-4 yourself in this
session (no fork).

## Step 2: Resolve Project

Use the project name passed as the argument. If none was passed, use the
project already loaded in this conversation (from
`/workspace:resume-project` or any earlier project interaction).

If no name was passed and no project is in context, tell the user a
project couldn't be resolved and ask which one they meant. Do not say
"notes are current" — nothing was actually checked.

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

If nothing to update, tell the user: "Nothing to update." and stop.

## Step 4: Apply Edits and Report

Resolve the absolute path to the consolidation script before writing:
`${CLAUDE_PLUGIN_ROOT}/scripts/consolidate-project.py`. Count CLAUDE.md's
current lines. If it already exceeds 100 lines, or applying the updates
from Step 3 would push it over, run
`python3 "<consolidation script path>" <name>` first, then re-read the
consolidated CLAUDE.md and apply the updates to that file instead. If the
script reports `over_threshold_no_sections`, manually move content into a
detail file before applying the update. If the update would still exceed
100 lines afterward, move the overflow into a detail file (add a
Reference Files row) and leave only an index-appropriate pointer line in
CLAUDE.md.

Apply the updates identified in Step 3 directly: only edit files under
the project directory, never change the `status:` frontmatter field, use
the Edit tool for existing files and Write for new files, and edit each
file individually — do not rewrite entire files.

If an Edit or Write call fails partway through, tell the user the update
failed with that error and suggest retrying `/workspace:update-project` —
never report success for a partially-applied set of edits.

Count CLAUDE.md's lines again and confirm to the user what was updated,
including the line-count change (e.g. "CLAUDE.md: 62 → 71 lines"). If the
session produced durable domain-level knowledge (not just project
status), suggest `/workspace:update-domain`.
