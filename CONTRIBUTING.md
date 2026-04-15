# Contributing to Edge Tooling

## Contribution Model

Fork the repo, push changes to your fork, and open a PR against `main`.

Reviews are managed through OWNERS/OWNERS_ALIASES. All PRs to `main` receive automated review from CodeRabbit (shellcheck, markdownlint, ruff). See `OWNERS_ALIASES` for the current reviewer list.

## What You Can Contribute

| Type | Location | Guide |
|------|----------|-------|
| New tool | `<tool-name>/` at repo root | [Adding a Tool](#adding-a-tool) below |
| Plugin | `plugins/<name>/` | [Plugin Contributing Guide](plugins/docs/CONTRIBUTING.md) |
| Bug fix / enhancement | Component directory | Follow component README |
| Documentation | Markdown files | [Code Standards](#code-standards) below |
| Environment template | `environments/<name>/` | Follow existing patterns |

## Commit Conventions

Format: `<type>(<scope>): <subject>`

**Types**: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`

**Scopes**: component name — `plugins`, `submodule`, `ec2-deploy`, `sno-deploy`, etc.

**Branch naming**: `<type>/<description>` — e.g., `feat/new-tool`, `fix/deploy-bug`, `plugin/my-plugin`

Examples:

```text
feat(plugins): add cluster-health plugin
fix(sno-deploy): correct subnet mask validation
docs: update prerequisites table
chore(submodule): update two-node-toolbox (abc1234 -> def5678, 5 commits)
```

## Code Standards

### Shell

- Shebang: `#!/usr/bin/bash`
- `set -euo pipefail`
- Quote all variables
- Must pass shellcheck

### Python

- PEP 8
- Must pass ruff

### YAML

- 2-space indentation
- Quote strings with special characters

### Markdown

- Must pass markdownlint
- Professional, terse, customer-centric — no emojis or filler

### General

- No hardcoded credentials — use environment variables
- Self-documenting code over comments
- First-pass code review is automated by CodeRabbit on all PRs to `main`

## Adding a Tool

1. Create a directory at repo root with a `Makefile` or `README.md`
2. Add a `README.md` documenting purpose, prerequisites, and usage
3. Update the tool table in root `CLAUDE.md`
4. Add the directory name to the `DOCUMENTED_TOOLS` array in `.claude/hooks/detect-new-tools.sh`
5. Commit: `feat(<tool-name>): add <tool-name>`

## Skills, Agents, and Commands

Plugin components serve different roles. Choosing the right one matters.

### When to Use Each

| Component | User-invocable | Purpose | Example |
|-----------|---------------|---------|---------|
| **Skill** | Yes (`/name`) | User-facing workflows, multi-step procedures, orchestration | `/microshift-ci:doctor` |
| **Agent** | No | Focused subtasks spawned by a skill, often run in parallel | `release-health:Epic Fetcher` |
| **Hook** | No | Event-driven automation (session start, tool validation) | `detect-new-tools.sh` |

**Use a skill** when a user needs to invoke it directly. Skills contain step-by-step instructions and can spawn agents or call other skills.

**Use an agent** when a skill needs to break work into parallel, isolated subtasks. Agents communicate with their parent skill through files (typically JSON). They don't interact with the user.

**Use a hook** when behavior should trigger automatically on an event (session start, before/after tool use).

### Grouping Related Skills

Use colon-based namespacing to group skills under a workflow domain. This keeps `/` autocomplete organized and signals that skills belong together.

**Pattern**: `<domain>:<action>`

```text
jira:create-epic       — enforces epic-specific field standards
jira:create-story      — enforces story-specific acceptance criteria format
jira:link-to-epic      — links stories to parent epics

microshift-ci:doctor   — orchestrates full CI analysis
microshift-ci:prow-job — analyzes a single Prow job
microshift-ci:test-job — analyzes a single test execution
```

Each grouped skill lives in its own directory under the plugin's `skills/` folder:

```text
plugins/jira/
├── .claude-plugin/
│   └── plugin.json
├── skills/
│   ├── create-epic/
│   │   └── SKILL.md        # name: jira:create-epic
│   ├── create-story/
│   │   └── SKILL.md        # name: jira:create-story
│   └── link-to-epic/
│       └── SKILL.md        # name: jira:link-to-epic
├── agents/
│   └── field-validator.md   # spawned by skills to validate fields
└── README.md
```

The plugin name (`jira`) becomes the namespace prefix. Individual skills define domain-specific standards (required fields, formatting rules, validation) within their `SKILL.md` instructions.

### Skill-Agent Orchestration

For complex workflows, skills orchestrate agents in phases:

1. Skill gathers input and configuration
2. Skill spawns agents (in parallel where possible), substituting `{VARIABLES}` in agent definitions
3. Agents write results to JSON files in a shared work directory
4. Skill reads agent outputs and synthesizes the final result

## Documentation for Agents

This repository uses Claude Code extensively. Contributors (human and agent) should maintain the following infrastructure.

### CLAUDE.md Files

- **Root CLAUDE.md**: repository overview, tool table, common workflows, prerequisites
- **Component CLAUDE.md**: per-tool guidance scoped to that directory
- **When to update**: adding/removing tools, changing workflows, modifying prerequisites
- **Style**: concise, intent-focused, no filler (see `global-claude.md`)

### Hooks

| Hook | Purpose |
|------|---------|
| `.claude/hooks/detect-new-tools.sh` | Flags undocumented tool directories at session start |
| `.claude/hooks/update-submodules.sh` | Checks for stale submodules at session start |
| `.claude/hooks/detect-new-plugins.sh` | Flags new plugins not yet in marketplace catalog |

When adding a tool, update the `DOCUMENTED_TOOLS` array in `detect-new-tools.sh`.

### Plugins

Plugins extend Claude Code capabilities for the team. For plugin contribution details:

- [Plugin Contributing Guide](plugins/docs/CONTRIBUTING.md)
- [Plugin Development Guide](plugins/docs/DEVELOPMENT.md)

## Review Process

- All PRs require review from `edge-reviewers` (see `OWNERS_ALIASES`)
- CodeRabbit provides automated review on PRs to `main`
- `two-node-toolbox/` is excluded from CodeRabbit review (external submodule)
- Reviewers check: code quality, security, documentation, and test coverage
