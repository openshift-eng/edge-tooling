---
name: new-project
description: Create a new project workspace for a development task (bug, feature, CI, docs, analysis)
argument-hint: [description]
---

# New Project

A project is `projects/<name>/` in the workspace: a lean `CLAUDE.md` index
plus detail files, tracked across sessions. `$ARGUMENTS` is an optional
task description.

## Step 1: Infer, Then Confirm Once

Read `dev-env.yaml` at the workspace root (`$CLAUDE_PROJECT_DIR`, or the
nearest ancestor with `dev-env.yaml`; if there is none, tell the user to
run `/workspace:setup-environment` and stop). A top-level `self:` block
means a single-repo self-workspace: the repo is implicit.

From the description and any JIRA key or URL, infer:

- **type**: `bug`, `feature`, `ci-testing`, `docs`, or `analysis`.
- **title**: a short human title.
- **repos** (multi-repo only): which `repos:` entries are relevant, and
  which of them the task will modify.
- **branch**: the JIRA key in lower case plus a short slug (for example
  `ocpedge-2608-multi-hypervisor`), else a slug of the title. Prefix `fix/`
  for `bug`.
- **worktrees**: for `bug`, `feature`, and `docs`, one per repo to modify
  (self-workspace: one self worktree). None for `ci-testing` and
  `analysis`, unless it is an analysis of a PR: then a PR worktree.

If there is no description, ask for one. Present all inferred values in
one AskUserQuestion round and let the user correct them. Ask separately
only for what you cannot infer (usually the relevant repos and which to
modify).

## Step 2: Create

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/project.py" new --type <type> \
  --title "<title>" [--jira <key-or-url>] [--repos r1,r2] \
  [--branch <branch> --worktrees r1 | --branch <branch> --self-worktree] \
  [--pr <number> --worktrees <repo>] [--links url1,url2]
```

The project name comes from the JIRA key, else a slug of the title; pass
`--name` to override. Run with `--dry-run` first if the name is not yet
agreed. On `status: exists`, offer `suggestion` or
`/workspace:resume-project`. Report any `warnings` (a worktree that
failed): work then happens in the main checkout.

## Step 3: Write the Content

The script wrote the frontmatter and the section skeleton. Edit
`claude_md` to fill in:

- `## Summary`: two or three sentences on the task and its goal.
- `## Plan`: a `- [ ]` checklist specific to this task, not a generic one.

Create a detail file only when you have content for it, and add its row to
the `| File | Content |` table under `## Reference Files`. Keep
`CLAUDE.md` an index of 100 lines or less.

Tell the user the project name, the worktree paths to use for code
changes, and the first task.
