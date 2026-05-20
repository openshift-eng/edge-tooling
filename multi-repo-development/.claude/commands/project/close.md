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

**1a. Resolve project name**

Extract the first token from `$ARGUMENTS`. Run
`scripts/resume-project.py <first-token>` via Bash (omit the token if
none was provided). Parse the JSON and handle by `status`:

- **`ok`** — use `project.name` as the target. Proceed to 1b.
- **`no_argument`** — check if a project was loaded earlier in this
  conversation (e.g., via `/project:resume`). If so, use that project
  name as the default: re-run the script with that name and proceed.
  Otherwise, present the first 3 `alternatives` as AskUserQuestion
  options plus "See all projects". Re-run with the chosen name.
- **`not_found`** / **`out_of_range`** — show `error_message`, present
  `alternatives` as a picker, re-run with chosen name.
- **`no_projects`** — show `error_message` and stop.

**1b. Check current status**

If `project.frontmatter.status` is `done`:
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

Closing Notes always go in CLAUDE.md (the index), not in detail files.

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
