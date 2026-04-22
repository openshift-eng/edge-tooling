# repo-standards

Repository compliance checking and scaffolding for agentic development standards.

## What It Does

Enforces a baseline set of repository artifacts and conventions that enable effective AI-assisted development. Checks run automatically at session start and surface missing or non-compliant files.

## Automatic Hooks (SessionStart)

Two hooks fire when a Claude Code session begins:

- **check-repo-artifacts** --- detects missing required files (README.md, CONTRIBUTING.md, AGENTS.md, .coderabbit.yaml) and warns if CLAUDE.md is not a symlink to AGENTS.md
- **check-agents-md-size** --- warns if AGENTS.md exceeds the 200-line limit

No action is taken automatically. Hooks report findings and suggest next steps.

## Skills

### `/repo-standards:scaffold-repo [directory]`

Interactive scaffolding for new or non-compliant repositories. Gathers project details, generates missing artifacts from templates, and creates the CLAUDE.md symlink.

### `/repo-standards:health-check [directory]`

Full compliance audit. Runs all checks (artifacts, AGENTS.md size, CodeRabbit config quality, CONTRIBUTING.md sections, architecture docs) and produces a pass/warn/fail report table.

## Laws

Policy content lives in `references/laws/`. Seven law files cover:

1. Required artifacts and pass/fail criteria
2. CONTRIBUTING.md template and required sections
3. AGENTS.md convention (vendor-neutral standard, symlink, size limit)
4. CodeRabbit configuration requirements
5. Architecture documentation recommendations
6. Upstream project documentation recommendations
7. New repository mandate (post-April 2026)

## Installation

```bash
/plugin marketplace add openshift-eng/edge-tooling repo-standards
```
