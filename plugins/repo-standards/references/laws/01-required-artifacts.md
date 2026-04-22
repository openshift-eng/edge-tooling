# Law 01: Required Artifacts

Every repository MUST contain the following files at the repository root.

## Required Files

| File | Purpose | Pass Criteria |
|------|---------|---------------|
| `README.md` | Foundational project context | File exists and is non-empty |
| `CONTRIBUTING.md` | Contribution conventions and workflows | File exists, contains required sections (see Law 02) |
| `AGENTS.md` | AI-specific guidance for agentic tools | File exists, under 200 lines (see Law 03) |
| `.coderabbit.yaml` | AI code review configuration | File exists, has `auto_review` enabled (see Law 04) |

## Recommended Files

| File | Purpose | When Applicable |
|------|---------|-----------------|
| `docs/architecture.md` | System design and component boundaries | Repos with multiple components (see Law 05) |
| `docs/upstream.md` | Upstream project relationship | Repos interacting with upstream projects (see Law 06) |
| OpenShift Test Extensions config | CI test configuration | Repos with OpenShift CI jobs |
| Cyborg team/repo data | Team metadata for AI tooling | Repos enrolled in Cyborg program |

## Pass/Fail

A repository PASSES the required artifacts check when all four Required files exist and meet their individual pass criteria. Missing any Required file is a FAIL.

Recommended files do not affect pass/fail status but are surfaced as warnings during health checks.
