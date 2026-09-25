---
name: update-project
description: Update project documentation from what was accomplished in this session
argument-hint: [name-or-number]
---

# Update Project Documentation

Update a project's documentation based on what was accomplished in the
current conversation. Dispatch a fork to do the work, then act on its
report.

## Scope Rules

**Update:** files under `projects/<name>/` in the workspace — CLAUDE.md
(index, checklists, progress) and detail files (investigation notes, test
results, plans, etc.).

**NEVER touch during this command:**
- The `status:` frontmatter field — use `/workspace:close-project` to change it
- Memory files (`memory/MEMORY.md`, `memory/project_*.md`)
- Internal session tasks (TaskCreate / TaskUpdate)
- Repo source files under `repos/`

## Step 1: Dispatch the Fork

Check `$ARGUMENTS` for a trailing `--auto` token (this is how the
auto-update cron job identifies itself — see `/workspace:auto-update`).
If present, strip it and remember this run is **loop-driven**; it
determines cron behavior in the final step. A manual invocation (no
`--auto` token) is never loop-driven, regardless of whether a loop
happens to be running.

Dispatch a fork (Agent tool, `subagent_type: "fork"`) to do Steps 2-4
below, passing the remaining argument (if any) as the project name. Do
not use a fresh agent and do not set a `model` override — a fresh agent
or a different model has no conversation context and forces a cold
re-read of everything already in context.

Give the fork this directive: Check whether the project's CLAUDE.md
*actual current content* is visible in your inherited context — not a
summary of an earlier edit (e.g. a prior fork's one-line `updated:`
report), which can be stale relative to what's on disk. If the full file
content is present, use it directly and do not re-read it. If only a
summary, partial content, or an earlier fork's report is present, read
the file fresh with the Read tool before editing — inherited context is
not a substitute for the file when it doesn't actually contain the file.
Otherwise, apply all edits with the fewest tool calls possible and do not
re-read files to verify. Target 4 to 6 turns. Do not call CronCreate,
CronList, or CronDelete — cron management happens in the main session
after you return.

The fork's only output is a one-line report: `updated: <what>`,
`nothing`, `unresolved`, or `failed: <error>`.

## Step 2: Resolve Project (fork)

Use the project name passed in Step 1's directive. If none was passed,
use the project already loaded in this conversation (from
`/workspace:resume-project` or any earlier project interaction). Do not
re-check `$ARGUMENTS` here — Step 1 already stripped the `--auto` token
from it, so any later read of `$ARGUMENTS` in this fork is stale.

If no name was passed and no project is in context, report `unresolved`
and stop.

## Step 3: Identify Updates (fork)

Review the conversation history and identify:

1. **Checklist items completed** — `- [ ]` items now done.
2. **New checklist items** — work discovered or queued.
3. **Detail file updates** — new findings, test results, or analysis to
   add to existing detail files, or new detail files to create.
4. **New detail files in Reference Files table** — files created in
   `projects/<name>/` not yet registered in CLAUDE.md's table.
5. **Progress entries** — milestones or outcomes to append.
6. **`last-active` timestamp** — always update `last-active: <YYYY-MM-DDTHH:MM>`
   in the frontmatter to the current date and time when any other update is
   applied. If the field does not exist yet, add it after the `status:` line.

If nothing to update, report `nothing` and stop.

## Step 4: Apply Edits and Report (fork)

Apply the updates identified in Step 3 directly: only edit files under
the project directory, never change the `status:` frontmatter field, use
the Edit tool for existing files and Write for new files, and edit each
file individually — do not rewrite entire files.

If an Edit or Write call fails partway through, stop and report
`failed: <error>` — never report `updated:` for a partially-applied set
of edits.

Report exactly one line: `updated: <brief summary of what changed>`.

## After the Fork Returns (main session)

**This section fires on a different trigger than Step 1.** Step 1 ends
the dispatch turn once the fork is launched — `subagent_type: "fork"`
always runs in the background, so the dispatching turn cannot see its
result and must not act on this section yet. The instructions below
execute in a **separate, later turn**, entered only when the fork's
completion arrives as a new user-role notification message. Two trigger
points, not one continuous flow:

- **Dispatch turn** (Step 1): launch the fork, then stop. Do not guess,
  predict, or fabricate what the fork will report.
- **Notification turn** (this section): triggered by the fork's
  notification landing. Read the report value it contains and act on the
  matching branch below.

If the user asks a question before the notification has arrived, say the
update is still running — do not answer as if a report value is already
known.

The fork reports one of these values:

- `updated: <what>` — confirm to the user what was updated. If the
  session produced durable domain-level knowledge (not just project
  status), suggest `/workspace:update-domain`.
- `nothing` — tell the user: "Nothing to update."
- `unresolved` — tell the user a project couldn't be resolved and ask
  which one they meant. Do not say "notes are current" — nothing was
  actually checked.
- `failed: <error>` — tell the user the update failed with that error
  and suggest retrying `/workspace:update-project` manually.
- No report at all (the Agent tool call itself errored) — treat the same
  as `failed: <error>`, using whatever error the tool call surfaced.

**Cron management only applies when this run is loop-driven** (Step 1).
A manual invocation stops here — never call CronCreate, CronList, or
CronDelete for a manual run, even if a loop happens to be active.

If loop-driven:
- Fork reported `updated: ...` — arm a new one-shot cron: compute the
  date/time 50 minutes from now (`date`) and call CronCreate with
  `recurring: false`, cron pinned to that exact
  minute/hour/day-of-month/month (not `*/50` — cron reads that as
  minutes 0 and 50), prompt `/workspace:update-project <name> --auto`.
  If CronCreate fails, tell the user the update was saved but re-arming
  auto-update failed with that error, and to run `/workspace:auto-update`
  to re-enable. Do not claim the loop is still running.
- Fork reported `nothing` — do not re-arm. Tell the user: "Notes are
  current. Auto-update stopped. Run `/workspace:auto-update` to
  re-enable."
- Fork reported `unresolved` — do not re-arm. Ask the user which project
  they meant; auto-update has effectively stopped since the loop can no
  longer identify its target.
- Fork reported `failed: <error>`, or no report at all — tell the user:
  "Update failed: [error summary]. Run `/workspace:update-project`
  manually to retry." Do not re-arm.
