# Repo Standards Laws

Navigation index for repository agentic development standards. This file is **not normative** --- RFC 2119 language does not apply here. Rules live in `laws/`.

## Agent Task Index

Load only the law files your task requires. Do not read the entire `laws/` directory unless comprehensive coverage is explicitly needed.

| Task | Load These Files |
|------|-----------------|
| Scaffold Repo | All files in `laws/` |
| Health Check | `laws/01-required-artifacts.md`, `laws/03-agents-md-convention.md`, `laws/04-coderabbit-config.md` |
| General Reference | All files in `laws/` |

## Law Files

| File | Topic |
|------|-------|
| [01-required-artifacts.md](laws/01-required-artifacts.md) | Required repo files and pass/fail criteria |
| [02-contributing-template.md](laws/02-contributing-template.md) | CONTRIBUTING.md template and required sections |
| [03-agents-md-convention.md](laws/03-agents-md-convention.md) | AGENTS.md convention, symlink policy, size limit |
| [04-coderabbit-config.md](laws/04-coderabbit-config.md) | .coderabbit.yaml configuration requirements |
| [05-architecture-docs.md](laws/05-architecture-docs.md) | Architecture documentation recommendations |
| [06-upstream-docs.md](laws/06-upstream-docs.md) | Upstream project documentation recommendations |
| [07-new-repo-mandate.md](laws/07-new-repo-mandate.md) | Post-April-2026 new repository mandate |
