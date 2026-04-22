# Law 07: New Repository Mandate

## Effective Date

April 1, 2026.

## Policy

All repositories created after the effective date are presumed 100% agentic. AI agents are expected participants in the development workflow, not optional add-ons.

## Requirements

New repositories MUST have from day one:

- All Required artifacts (Law 01): README.md, CONTRIBUTING.md, AGENTS.md, .coderabbit.yaml
- AGENTS.md with CLAUDE.md symlink (Law 03)
- Architecture documentation if the repo has multiple components (Law 05)
- Upstream documentation if the repo interacts with upstream projects (Law 06)

## No Grandfathering

New repositories receive no grace period. The full standard applies immediately at repository creation.

Do not plan to "add AGENTS.md later" or "set up CodeRabbit after launch." These artifacts are part of the repository scaffold, alongside LICENSE and .gitignore.

## Rationale

Retrofitting agentic standards is expensive. Teams spend hours reconstructing context that should have been captured at creation time. Starting with the full standard costs minutes; adding it later costs days.

## Enforcement

The `repo-standards` plugin SessionStart hooks automatically detect missing artifacts. The `/repo-standards:scaffold-repo` skill generates compliant scaffolding for new repositories.
