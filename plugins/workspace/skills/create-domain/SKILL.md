---
name: create-domain
description: Build a custom multi-repo workspace from arbitrary repos — gather repos, clone, and generate per-repo context collaboratively
argument-hint: "[project description]"
---

# Create a Custom Workspace

Build a multi-repo workspace from any set of repos. To use a ready-made
bundled domain instead, use `/workspace:setup-environment`.

The plugin at `${CLAUDE_PLUGIN_ROOT}` is read-only. The workspace `$WS` is
a separate directory. Pass `--workspace "$WS"` to every `setup.sh` call.

## Step 1: Location and Repos

1. Ask where the workspace goes (`~/Workspace/<name>` is a good default),
   expand `~`, and `mkdir -p "$WS"`.
2. Get the user's description of the project (from `$ARGUMENTS` if given).
3. Build the repo list with the user: pasted URLs, org/repo searches, or
   your suggestions from the description. Each repo needs `name`, `url`,
   `branch`, `category`, and `summary`.
4. Write `$WS/dev-env.yaml` with the schema in
   `${CLAUDE_PLUGIN_ROOT}/templates/dev-env.yaml.template`.
5. Ask whether to save this as a reusable domain. If yes, also create
   `$WS/domains/<name>/` with `domain.yaml` (name, description) and a copy
   of `dev-env.yaml`. A workspace domain shadows a bundled one with the same
   name.

## Step 2: Clone

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/setup.sh" --workspace "$WS" clone
```

This also applies the settings template, links repo skills, and writes
`$WS/CLAUDE.md` with the repo table.

## Step 3: Generate Repo Context

Skip repos that already have a `CLAUDE.md`; tell the user they keep their
own context. Ask whether to generate context for the others. If the user
declines, stop here: a repo with no context gets no file.

1. Dispatch one Explore agent per remaining repo, all in parallel. Put the
   user's project description and the repo's category and summary in each
   prompt, so that the findings are specific to the project. Ask for: the
   purpose in one sentence, 3 to 8 key paths, up to 5 test files, 3 to 5
   build and test commands, a summary of the native docs, and references
   to other repos in this workspace.
2. Show a table of repo and detected purpose. Ask which repos get a full
   context file.
3. Read `${CLAUDE_PLUGIN_ROOT}/skills/create-domain/context-template.md`
   once. For each selected repo, draft a context file from the findings
   and that template, show it, and ask: approve, edit (one round), or
   skip. Write approved files to `$WS/repos/<repo>/CLAUDE.md`.

Finish with the workspace path, the repos and which got context, and a
reminder to launch `claude` from `$WS`.
