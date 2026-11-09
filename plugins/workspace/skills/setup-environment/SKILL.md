---
name: setup-environment
description: Set up or refresh a workspace — multi-repo from a domain (pick a location, clone repos, distribute context) or a single-repo self-workspace wrapping the current checkout
argument-hint: "[<git-url>]"
---

# Set Up Workspace Environment

The plugin at `${CLAUDE_PLUGIN_ROOT}` is read-only. The workspace `$WS` is
a separate directory. Pass `--workspace "$WS"` to every `setup.sh` call,
because the current directory is the launch directory, not `$WS`.

Below, `setup.sh` means `bash "${CLAUDE_PLUGIN_ROOT}/scripts/setup.sh"`.

## Step 1: Detect the Mode

If `$ARGUMENTS` is a git URL, it is an external domain: go to Step 2 with
that URL as the domain. Otherwise run `setup.sh detect` and act on `mode`:

- `self`: already a single-repo workspace. Tell the user, and stop.
- `domain`: an existing domain workspace at `root`. Offer to refresh it
  (`setup.sh --workspace "$WS" refresh-domain`, then Step 3) or to choose a
  different domain (Step 2).
- `custom`: an existing custom workspace. Offer to re-run Step 3.
- `repo`: inside a git repo. Ask: wrap this repo as a single-repo
  workspace (go to Self-Repo Path with `$WS` = `root`), or make a
  multi-repo workspace (Step 2).
- `new`: Step 2.

## Step 2: Multi-Repo: Location and Domain

Ask where the workspace goes (`~/Workspace/<name>` is a good default),
expand `~`, and set `$WS`. Run `setup.sh --workspace "$WS" init` to list
the domains, and ask the user to pick one, or an external domain by git
URL (a `#subdir` fragment selects a domain in a multi-domain pack). Then:

```bash
setup.sh --workspace "$WS" init <domain-name-or-url>
```

## Step 3: Clone

```bash
setup.sh --workspace "$WS" clone
```

This can take a long time; tell the user. `clone` copies each repo's domain
context to `DOMAIN-CONTEXT.md`, adds a supplemental `CLAUDE.md` only where
the repo has none, applies the settings template, links repo skills into
`.claude/skills/`, and writes the repo table in `$WS/CLAUDE.md`.

Summarize from the output: repos cloned, context files added, skills
linked or skipped, and domain docs. Tell the user to launch `claude` from
`$WS` for future sessions.

## Self-Repo Path

Never read or modify the repo's own `CLAUDE.md`.

1. Suggest a one-line summary from the repo's `README.md`. Ask the user to
   confirm the summary, the workspace name (the repo basename, unless it is
   generic such as `src`), and whether `projects/` is tracked in git
   (recommended) or ignored.
2. Run:

   ```bash
   setup.sh --workspace "$WS" init --self <name> --summary "<summary>" \
     [--ignore-projects]
   ```

3. Tell the user to launch `claude` from `$WS` and to start tasks with
   `/workspace:new-project`. Print, but do not write, this optional
   snippet for their repo's `CLAUDE.md`:

   ```markdown
   ## Task Workflow

   Per-task context lives under `projects/<task>/`, managed by the
   `workspace` plugin: `/workspace:new-project` scaffolds a task,
   `/workspace:resume-project` reloads one.
   ```
