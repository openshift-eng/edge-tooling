---
description: Close a project workspace and mark it as done
argument-hint: [name-or-number] [closing notes]
---

# Close Project Workspace

You are helping a developer close a project workspace by marking it as
done. This updates the project's CLAUDE.md frontmatter and optionally
records closing notes.

Everything after "close" in `$ARGUMENTS` is parsed as follows:
- The **first token** is an optional project name or numeric shorthand.
- Everything after the first token is treated as **closing notes**.

## Step 1: Select Project

**1a. Determine project name**

Handle the argument using the same cases as `/project:resume`:

**Case A — Numeric shorthand** (e.g., `/project:close 2`):
If the first token is a plain integer N, look in your conversation
context for the numbered "Recent projects" table produced by the
SessionStart hook. Pick the project name on row N from that table.
If the table is not in context, fall back to running
`scripts/recent-projects.py --names` and pick the Nth line.
If N is out of range, show an error and fall through to Case C.

**Case B — Project name** (e.g., `/project:close OCPBUGS-74679`):
If the first token is a non-numeric string, use it as the target
project name.

**Case C — No argument** (`/project:close`):
Look in your conversation context for the "Recent projects" table.
If present, extract project names that have a non-done status and
present them as AskUserQuestion options. Include a "See all projects"
option. If no table is in context, run `scripts/recent-projects.py
--names` and present those instead. If the user picks "See all
projects", list all project directories and present as a second
AskUserQuestion.

**1b. Validate project exists**

Check that `projects/<name>/` exists. If not:
- Show an error: "Project `<name>` not found."
- List all available projects and ask the user to pick one.

**1c. Check current status**

Read the project's `CLAUDE.md` and parse the frontmatter. If the
status is already `done`:
- Inform the user: "Project `<name>` is already marked as done."
- Ask if they'd like to update the closing notes anyway. If no, stop.

## Step 2: Gather Closing Notes

**2a. Extract notes from arguments**

If there is text after the project identifier in `$ARGUMENTS`, use it
as the closing notes.

**2b. Ask for notes**

If no notes were provided in the arguments, ask the user:

> "Any closing notes for this project? (outcome, resolution, links to
> PRs, etc.) Say 'no' to skip."

## Step 3: Update Project CLAUDE.md

**3a. Read the current CLAUDE.md**

Read the full `projects/<name>/CLAUDE.md` file.

**3b. Update frontmatter fields**

Using the Edit tool, update the YAML frontmatter:

1. Change `status: active` (or whatever the current status is) to
   `status: done`
2. Add a `closed: <YYYY-MM-DD>` field (today's date) after the
   `status` line. If a `closed:` field already exists, update it.

**3c. Add closing notes section**

If the user provided closing notes (non-empty, not "no"):

1. Check if a `## Closing Notes` section already exists in the file.
2. If it exists, replace its content with the new notes.
3. If it doesn't exist, add a `## Closing Notes` section at the end
   of the file with the notes and today's date:

```markdown
## Closing Notes

_Closed YYYY-MM-DD_

<user's closing notes>
```

## Step 4: Confirm Closure

Display a brief confirmation:

```
Project `<name>` marked as done.
```

If closing notes were added, include them in the confirmation.
Remind the user that closed projects won't appear in the SessionStart
summary, but can still be resumed with `/project:resume <name>`.

---

## Important Notes

- Always use the Read tool before editing, and Edit tool for changes
- Never delete the project directory — closing just updates metadata
- Use today's date for the `closed` field
- The project will be filtered from the SessionStart "Recent projects"
  table but remains fully accessible via `/project:resume`
