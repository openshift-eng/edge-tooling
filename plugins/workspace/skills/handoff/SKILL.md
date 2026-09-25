---
name: handoff
description: Save session progress to the project docs and arm a handoff for the next /clear
argument-hint: [name]
disable-model-invocation: true
---

# Hand Off a Session

Record what this session accomplished, then arm a handoff so the next
session — after you press `/clear` — resumes the project automatically.

This is the command to run at a natural breaking point. It replaces the
`/workspace:update-project` → `/clear` → `/workspace:resume-project`
sequence with `/workspace:handoff` → `/clear`.

## Step 1: Resolve Project

Use the project already loaded in this conversation (from
`/workspace:resume-project` or any earlier project interaction). If
`$ARGUMENTS` has a token, use that as the project name instead.

If no project is in context and no argument was given, ask which project.

**Do not run `resume-project.py` here.** This session is the one being wound
down; loading its JSON merely to learn a name spends the context this
command exists to save.

## Step 2: Update the Documentation

Invoke the `workspace:update-project` skill with the resolved project name.

All document writing happens there, under its existing scope rules — including
its prohibition on touching `status:` frontmatter, memory files, and repo
source. Do not duplicate or second-guess that work here.

If update-project reports it had nothing to update, continue anyway: a
handoff is still worth arming.

## Step 3: Decide the Handoff

Read the project's current CLAUDE.md (Read tool). `update-project` runs
its edits in a fork and only returns a one-line summary to this session —
the actual edits, including any new detail files or Reference Files
table rows, are not otherwise visible here. The file on disk is the only
reliable source for what changed.

From that file, decide two things:

1. **`next_task`** — the single next action, in a short phrase. This is your
   judgment about what should happen next, not merely the first unchecked
   checklist item. It is the thing that would otherwise be lost across the
   `/clear`.
2. **`load_files`** — the detail files needed for that task, as they appear
   in the Reference Files table (paths relative to the project directory).
   An empty list is fine for a monolithic project.

## Step 4: Arm the Handoff

Run via Bash, substituting your values:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" write \
  --project "<name>" \
  --next-task "<next task phrase>" \
  --load-files "<file1.md,file2.md>"
```

Omit `--load-files` when there are none.

Parse the JSON output:

- **`status: "ok"`** — proceed to Step 5.
- **`status: "error"`** — show the `message` and **stop**. Do not tell the
  user to clear: the documentation updates from Step 2 are safely on disk,
  and `/workspace:resume-project` by hand still recovers everything.

## Step 5: Report

Tell the user:

> Handoff armed. Press `/clear` — the next session will resume
> `<project>` at "<next task>" automatically.

The handoff expires after 60 minutes and fires only once.
