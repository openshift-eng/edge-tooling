# Law 03: AGENTS.md Convention

## Standard

AGENTS.md is the vendor-neutral AI agent instruction standard. Originally released by OpenAI in August 2025, it was contributed to the Agentic AI Foundation (a directed fund under the Linux Foundation) in December 2025. See the [supported tools list](https://github.com/agentsmd/agents.md#supported-tools) for current tool coverage.

Every repository MUST use `AGENTS.md` as the primary agent instruction file.

## Symlink Policy

`CLAUDE.md` MUST be a symlink to `AGENTS.md`:

```bash
ln -s AGENTS.md CLAUDE.md
```

Do NOT maintain separate files. Do NOT copy content between them. The symlink ensures all tools read identical instructions.

## Size Limit

AGENTS.md MUST be under 200 lines.

Large instruction files degrade AI performance --- models spend context on boilerplate instead of your code. Use just-in-time data loading for detailed context:
- Reference files (`references/`) for lookup tables and detailed specs
- Skill files for workflow-specific instructions
- Inline comments in code for local context

## Required Content

AGENTS.md MUST contain:
- **Project overview** --- what the project does, in 2--3 sentences
- **Build/test/lint commands** --- exact commands to build, test, and lint
- **Code style** --- formatter, linter, naming conventions
- **PR/commit format** --- branch naming, commit message conventions
- **Security considerations** --- secrets handling, auth patterns, sensitive paths

## Anti-Patterns

- Pasting entire style guides into AGENTS.md
- Duplicating README content
- Including environment-specific setup (use .env or local config)
- Embedding large lookup tables (use reference files instead)
