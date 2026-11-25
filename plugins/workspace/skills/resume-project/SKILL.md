---
name: resume-project
description: Resume an existing project workspace — reload its context and continue work
argument-hint: [name-or-number]
---

# Resume Project

## Step 1: Resolve

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resume-project.py" $ARGUMENTS`.
If `status` is not `ok`, do what `error_message` says. Otherwise call the
`project` object `P`. All paths in `P` are absolute.

## Step 2: Load the Index

Read `P.context_file` and, if set, `P.domain_context` (a short domain
orientation). Do not read `P.repo_context_files`, `P.domain_docs`, or
detail files yet.

## Step 3: Check Worktrees

`P.worktree_status` has one entry per worktree, with `status_text`. For a
`MISSING` worktree, offer to recreate it:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/project.py" worktree create \
  (--repo <repo> | --self) --branch <branch>   # or --pr <N> for pr/<N>
```

The entry's `repo` is `(self)` for a self-workspace worktree.

## Step 4: Summarize and Pick the Next Task

Give a short summary from `P`: type, JIRA, checklist progress
(`P.checklist`), worktrees that need attention, and unregistered files.

If the session context has a handoff for this project ("Handoff
pending"), the next task is already chosen: read the detail files it
names and start that task. Otherwise offer the next tasks from
`P.checklist.unchecked_items` in one AskUserQuestion, each with the detail
files that match it in `P.reference_files` (paths are relative to
`P.dir`).

## Step 5: Load Lazily

After the user picks, read only the matched detail files. Read a repo's
entry in `P.repo_context_files` when work moves into that repo, and a
`P.domain_docs` entry when the task needs architecture or debugging
depth. For code changes, use the worktree path, not the main checkout.
