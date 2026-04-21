# Coderabbit

[← AI Best Practices](../README.md)

Coderabbit is an AI-powered code review service that automatically reviews pull requests. It runs as a GitHub integration -- no local installation required. When a PR is opened or updated, Coderabbit analyzes the changes and posts review comments directly on the PR.

## Setup and Configuration

### Repository Configuration

Coderabbit is configured per-repository via `.coderabbit.yaml` at the repo root. Key settings:

```yaml
reviews:
  auto_review:
    enabled: true
    drafts: false            # Skip draft PRs
  path_filters:
    - "!docs/**"             # Exclude documentation-only changes
    - "!**/*.md"
    - "!vendor/**"
  tools:
    - name: golangci-lint
      enabled: true
    - name: shellcheck
      enabled: true
```

**`auto_review`** -- Controls whether Coderabbit reviews PRs automatically. Enable for all repos. Disable for drafts to avoid noise during work-in-progress.

**`path_filters`** -- Exclude paths that don't benefit from AI review (generated code, vendored dependencies, documentation). Use `!` prefix to exclude.

**`tools`** -- Enable static analysis tools that Coderabbit should run alongside its AI review. These provide deterministic checks that complement the AI analysis.

### Review Rules

Use the `instructions` field to provide Coderabbit with project-specific coding standards and review focus areas:

```yaml
reviews:
  instructions: |
    - Follow Go error handling conventions: wrap errors with fmt.Errorf and %w.
    - Bash scripts must use set -euo pipefail.
    - Kubernetes manifests must include resource requests and limits.
    - Commit messages follow conventional commits format (feat/fix/chore/docs).
    - Flag any hardcoded credentials, API keys, or secrets.
```

These instructions are injected into every review. Keep them concise and specific to your project's conventions. Generic advice ("write clean code") wastes tokens and produces vague feedback.

### Data Exposure

Coderabbit is a cloud-hosted service. Understand what it sees:

| Data Sent | Details |
|-----------|---------|
| PR title and description | Full text of the PR body |
| Changed files | Full diff of all files modified in the PR |
| Surrounding context | Adjacent code around changed lines for understanding |
| Repository metadata | Branch names, file paths, commit messages |

**Not sent:** Files not modified in the PR, local environment variables, secrets (unless committed in the diff).

Follow the team's data protection rules: do not include confidential data, API keys, or customer information in PR diffs. If a PR touches sensitive files, verify they are excluded via `path_filters` or review the Coderabbit output carefully for any data echoed back in comments.

## Usage

### Complement, Not Replacement

Coderabbit automates a first pass. It does not replace human review. Understand the boundary:

| Coderabbit Catches | Coderabbit Misses |
|--------------------|-------------------|
| Style violations and convention drift | Architectural fitness -- whether the approach is right |
| Common bug patterns (nil checks, error handling) | Business logic correctness |
| Security anti-patterns (hardcoded secrets, SQL injection) | Performance implications at scale |
| Missing tests for new code paths | Whether the change solves the actual problem |
| Inconsistent naming and formatting | Cross-repo or cross-service impact |
| Unused imports and dead code | Team context -- why a decision was made |

Treat Coderabbit findings as a checklist to address before requesting human review, not as a substitute for it.

### Interpreting Suggestions

Coderabbit posts comments at three levels. Respond appropriately:

**Accept** -- The suggestion is correct and actionable. Fix it and push. Examples: missing error handling, unused variables, style violations that match project conventions.

**Question** -- The suggestion seems reasonable but you're unsure, or it conflicts with an intentional design choice. Reply to the comment with context. This helps Coderabbit learn (via feedback) and documents the decision for human reviewers.

**Dismiss** -- The suggestion is wrong, irrelevant, or conflicts with project requirements. Resolve the comment. Don't leave incorrect suggestions unaddressed -- future reviewers may assume they're valid.

When Coderabbit flags something you intentionally wrote that way, reply with a brief explanation. This is useful documentation regardless of the AI review.

### Configuring Review Focus

Adjust `.coderabbit.yaml` to match your project's priorities:

- **Security-sensitive repos** -- Add instructions emphasizing input validation, authentication checks, and secret detection.
- **Infrastructure/IaC repos** -- Focus on resource limits, RBAC, and configuration drift.
- **Library/SDK repos** -- Focus on API compatibility, documentation, and backward compatibility.
- **Rapid prototyping** -- Relax style checks, keep security and correctness checks.

Update `instructions` as project conventions evolve. Stale instructions produce irrelevant feedback.

### PR Workflow Integration

Coderabbit fits into the standard PR workflow as an automated first reviewer:

1. **Create PR** -- Open the pull request with a clear title and description. Coderabbit uses these to understand intent.
2. **Coderabbit reviews** -- Within minutes, Coderabbit posts review comments on the PR. Review these before requesting human reviewers.
3. **Address feedback** -- Fix valid findings, reply to questionable ones, dismiss incorrect ones.
4. **Push updates** -- Coderabbit re-reviews the new changes automatically.
5. **Human review** -- Request human review once Coderabbit findings are addressed. Human reviewers can focus on architecture, logic, and context rather than style and basic correctness.

This workflow reduces review round-trips. Human reviewers spend less time on mechanical issues and more time on design and correctness.

**Tip:** Write descriptive PR titles and descriptions. Coderabbit uses them to understand the intent of the change, which improves the relevance of its suggestions.
