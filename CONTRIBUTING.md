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