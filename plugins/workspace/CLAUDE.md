# CLAUDE.md

Guidance for Claude Code when working **on this repository** — the source of
the `workspace` Claude Code plugin.

> This file is developer documentation for the plugin itself. It is **not**
> the CLAUDE.md a user gets in their workspace — `setup.sh clone` generates
> that one from `templates/workspace-CLAUDE.md.template`.

## What This Repo Is

The **`workspace`** plugin: an installable Claude Code plugin that bootstraps
and manages single- and multi-repo dev environments for AI-assisted development. Repo root
= plugin root.

Two roots are kept strictly separate:

- **Plugin root** (this repo) — shipped, read-only. Scripts self-derive it
  from their own location; skills reference it via `${CLAUDE_PLUGIN_ROOT}`.
- **Workspace root** — user-chosen, mutable. Holds `dev-env.yaml`, `repos/`,
  `projects/`, and workspace-local `domains/`. Resolved at runtime, never
  from `__file__`.

## Layout

```text
.claude-plugin/plugin.json                        Plugin manifest (marketplace.json is at the repo root)
skills/<name>/SKILL.md                            9 skills (workspace: prefix); see README.md
skills/create-domain/context-template.md          Context-file template
hooks/hooks.json                                  SessionStart → recent-projects.py / handoff.py
scripts/setup.sh                                  detect/init/clone/update/status/list/refresh-domain
scripts/workspace_lib.py                          Shared, yaml-free at import: workspace root, frontmatter, worktree status
scripts/project.py                                new / worktree create|status / close / touch
scripts/resume-project.py                         Resolve a project and return its context as JSON
scripts/recent-projects.py                        SessionStart hook: recent projects table
scripts/consolidate-project.py                    Archive completed checklist items
scripts/domain-info.py                            Project→domain resolution, copy-on-write, --record
scripts/skills.py                                 Repo-skill symlinks (scan/link/link-all)
scripts/handoff.py                                Handoff marker: write (skill) / read (hook) / clear
domains/{example,tnf,lvm-operator}/               Bundled domains (read-only)
templates/                                        dev-env templates, settings.local.json.tpl, workspace-CLAUDE.md.template
tests/test_setup.sh, tests/test_*.py              Test suites (bash; stdlib unittest)
```

Skills hold judgment and questions to the user. Steps with one correct
result belong in a script that prints JSON; each non-ok result carries an
`error_message` that tells the skill what to do.

## Key Conventions

- **Workspace resolution** (`setup.sh` and `workspace_lib.py`, identical
  order): explicit `--workspace`/`WORKSPACE_ROOT` → `CLAUDE_PROJECT_DIR` →
  walk up from cwd to the nearest `dev-env.yaml` → error / None. No
  `__file__` fallback.
- **Domain lookup** everywhere: workspace `domains/` first, then
  plugin-bundled `domains/`.
- **PyYAML**: plugins can't declare python deps. The scripts emit a
  self-describing JSON error when PyYAML is missing. `workspace_lib.py`
  imports it lazily, and `recent-projects.py` and `handoff.py` never use
  it, so the SessionStart hook never needs it.
- **Repo skills** are linked into `<workspace>/.claude/skills/` once, by
  `setup.sh clone` (`skills.py link-all`). The links belong to the
  workspace, not to a project. A name in more than one repo, or already in
  use, is skipped.
- **Session handoff**: `/workspace:handoff` writes a single-use marker
  to `<workspace>/.claude/handoff.json`; the SessionStart hook on the `clear`
  matcher (`handoff.py read`) consumes it and tells Claude to resume that
  project, then falls through to `recent-projects.py` when no handoff is
  armed. Marker TTL 60 min, schema version 1. `handoff.py` is yaml-free for
  the same reason `recent-projects.py` is. `/clear` itself can never be
  issued by Claude — it is not among the built-ins reachable through the
  Skill tool — so the marker is how state crosses that boundary.
- Python scripts target **python3.9+** (macOS system python); they use
  `from __future__ import annotations` so `X | None` hints don't break there.
- **Single-repo self-workspaces**: a top-level `self:` block (`name`,
  `summary`) in `dev-env.yaml` marks a workspace whose root IS the wrapped
  repo checkout (`setup.sh init --self`). Mutually exclusive with
  `domain:`; `repos:` keeps its normal meaning (usually `[]`). Detection
  in bash is grep-based (`has_self_block`). The plugin never touches the
  wrapped repo's CLAUDE.md.

## Dev Loop

```bash
# Load the plugin from this checkout without installing it
claude --plugin-dir .
# After editing skills/hooks/manifests, reload without restarting
/reload-plugins

# Run the test suites
bash tests/test_setup.sh
for t in tests/test_*.py; do python3 "$t"; done

# Validate the plugin manifest
claude plugin validate . --strict
```

When dogfooding the project tooling against this repo's own `projects/`,
set `WORKSPACE_ROOT` (or `CLAUDE_PROJECT_DIR`) to the repo root, since it has
no `dev-env.yaml` to anchor walk-up resolution.

## Contributing

This plugin lives in `plugins/workspace/` within the
[edge-tooling](https://github.com/openshift-eng/edge-tooling) repo.
PRs use the fork model — push to your fork, open a PR against `main`.

Bump the version in **both** `plugins/workspace/.claude-plugin/plugin.json`
and `.claude-plugin/marketplace.json` on every PR that changes plugin
behavior or documentation.
