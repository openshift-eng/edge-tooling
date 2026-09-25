# workspace — Workspace Manager (Claude Code plugin)

An installable [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview)
plugin for AI-assisted development. It works with a single repo or many —
install it once, then run `/workspace:setup-environment` to scaffold a
workspace with structured project tracking and long-running task management.

**Single-repo** — run setup inside any git checkout to wrap it as a
self-workspace. No cloning, no domains; you get project tracking and
session handoff on top of your existing repo.

**Multi-repo** — declare the repos you need via a **domain** (or a custom
config), and the plugin clones and organizes them, layers per-repo Claude
context on top, and gives you a unified workspace across all of them. Ships
with bundled domains for common scenarios (the included ones target OpenShift
components) — or build your own.

## Install

From inside Claude Code:

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install workspace@edge-tooling
```

## Quick Start

```text
/workspace:setup-environment
```

The skill walks you through everything. Inside a git repo it offers to wrap
it as a **single-repo self-workspace** — no cloning needed. Otherwise it
asks **where** to create a multi-repo workspace, lets you pick a **domain**
(or an external one by git URL), clones the repos, distributes context
files, and generates the workspace's root `CLAUDE.md`. Either way, launch
`claude` from the workspace directory for future sessions.

To build a multi-repo workspace from an arbitrary set of repos instead of a
bundled domain, use `/workspace:create-domain`.

## Way of Working

A workspace is a long-lived directory. Projects inside it track individual
tasks — a bug fix, a feature, an investigation — across as many Claude Code
sessions as they need.

**The loop:**

```text
                    ┌────────────────────────────┐
                    │                            │
                    ▼                            │
/new-project ──▶ <work> ──▶ /update-project ──▶ /resume-project
                                │
                                ▼
                          /close-project
```

| Step | What happens |
|------|-------------|
| **new-project** | Creates a `projects/<name>/CLAUDE.md` with the task description, checklist, and links to relevant repos. Optionally sets up a git worktree for isolated work. |
| **work** | Normal development. The project CLAUDE.md keeps you oriented — Claude loads it when you resume. |
| **update-project** | Records what the session accomplished: checked-off items, decisions, blockers. Run this before ending a session. |
| **resume-project** | Reloads the project context in a new session. The SessionStart hook also surfaces your recent projects automatically. |
| **close-project** | Marks the project done and cleans up any worktrees it created. |

For single-session tasks you can skip the loop — just work directly. Projects
pay off when a task spans multiple sessions or you need to context-switch
between efforts.

**Handoffs.** When you want the next session to pick up exactly where you
left off, use `/workspace:handoff` before `/clear`. The next session
auto-resumes that project instead of showing the project list.

**Versioning projects.** The `projects/` folder is gitignored by default.
If you want to track it in git, use a separate private repo — project docs
often contain internal context (decisions, blockers, task notes) that
shouldn't live in the repos you contribute to.

## Skills

**Core workflow** — the project lifecycle:

| Skill | Purpose |
|-------|---------|
| `/workspace:new-project` | Start a project for a task (bug, feature, docs, analysis) |
| `/workspace:update-project` | Record session progress into the project docs |
| `/workspace:resume-project` | Reload a project's context and continue |
| `/workspace:close-project` | Mark a project done and clean up worktrees |
| `/workspace:handoff` | Arm a handoff so the next `/clear` auto-resumes |
| `/workspace:consolidate-project` | Archive completed checklist items from a large project |
| `/workspace:auto-update` | Automatically save project notes within 50 minutes of any work, during idle |

**Workspace setup** — usually one-time:

| Skill | Purpose |
|-------|---------|
| `/workspace:setup-environment` | Create or refresh a workspace from a domain, or wrap a single repo |
| `/workspace:create-domain` | Build a custom domain from arbitrary repos |
| `/workspace:update-domain` | Feed lessons from a project back into the domain's context files |

A SessionStart hook surfaces your recent projects whenever you launch Claude
Code inside a workspace (it stays silent elsewhere). After
`/workspace:handoff`, that same hook instead resumes the handed-off
project on your next `/clear`.

## Concepts

- **Plugin root** — where the plugin ships (read-only). You never edit here.
- **Workspace root** — the directory you choose during setup (or the repo
  root in a self-workspace). Always contains `dev-env.yaml` and `projects/`.
  Multi-repo workspaces also have `repos/` and workspace-local `domains/`.
- **Domain** (multi-repo only) — a reusable config: a `dev-env.yaml` repo
  list plus optional per-repo context, supplemental CLAUDE.md files, and
  docs.

### Domains

- **Bundled** — ship with the plugin (`tnf`, `lvm-operator`, `example`).
- **External** — installed from a git URL into your workspace's `domains/`:
  `/workspace:setup-environment` → "External domain (git URL)". A URL may
  include a `#subdir` fragment for packs holding multiple domains.
- **Authoring** — a domain is a directory with `domain.yaml` (name +
  description), `dev-env.yaml` (repos), and optional `context/<repo>.md`,
  `supplemental/<repo>.md`, `docs/`, and `settings.local.json.tpl`. Workspace
  domains shadow bundled ones of the same name.

## dev-env.yaml

Generated in your workspace by the setup skills. The schema depends on the
workspace mode.

**Single-repo (self-workspace):**

```yaml
self:
  name: my-repo            # workspace name (usually the repo basename)
  summary: "Brief description of the repo"

repos: []                  # initially empty; optional reference repos
```

**Multi-repo (domain workspace):**

```yaml
domain:                    # auto-recorded by setup; enables refresh-domain
  name: <domain-name>
  source: bundled          # 'bundled' or the external git URL
  # ref / subdir            # (external only)

repos:
  - name: my-repo          # identifier and directory name under repos/
    url: https://github.com/org/my-repo.git
    branch: main
    category: development  # docs | development | testing | deployment | troubleshooting
    summary: "Brief description of the repo's role"
    directory: my-repo     # (optional) overrides the directory name
```

Multi-repo workspaces clone with `--filter=blob:none` (blobless): full
structure visible, blob contents fetched on demand.

## Requirements

- Git (2.27+ for blobless clones)
- Python 3.9+ with **PyYAML** (`pip3 install pyyaml`), *or* `yq` — needed to
  parse `dev-env.yaml`/project frontmatter. The project tooling reports a clear
  message if PyYAML is missing.
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview)

## Upgrading from the pre-plugin version

Earlier versions were cloned-and-run and installed a SessionStart hook into
your workspace's `.claude/settings.local.json`. The hook now ships with the
plugin, so **remove the stale `hooks` block** from that file to avoid a
duplicate/failing hook:

```jsonc
// delete this block from .claude/settings.local.json
"hooks": {
  "SessionStart": [ { "hooks": [ { "type": "command",
    "command": "\"$CLAUDE_PROJECT_DIR\"/scripts/recent-projects.py" } ] } ]
}
```

The old `/dev-env-setup` and `/project:*` commands are replaced by the
`/workspace:*` skills above.

## Developing the plugin

See [CLAUDE.md](CLAUDE.md) for the layout, dev loop
(`claude --plugin-dir .` + `/reload-plugins`), and test command
(`bash tests/test_setup.sh`).
