# Law 04: CodeRabbit Configuration

## Required

`.coderabbit.yaml` MUST exist at the repository root.

`.coderabbit.yaml` MUST have an `auto_review` section with reviews enabled:

```yaml
reviews:
  auto_review:
    enabled: true
```

## Recommended

### Path Filters

SHOULD exclude non-code paths from review:

```yaml
reviews:
  path_filters:
    - "!docs/**"
    - "!*.md"
    - "!vendor/**"
    - "!**/testdata/**"
```

### Instructions

SHOULD include project-specific review rules:

```yaml
reviews:
  instructions: |
    Focus on error handling, security, and API compatibility.
    Flag any hardcoded credentials or secrets.
    Verify unit test coverage for new functions.
```

### Static Analysis Tools

SHOULD reference valid static analysis tools appropriate for the project language:

- Go: `golangci-lint`
- Shell: `shellcheck`
- Python: `ruff`, `mypy`
- YAML: `yamllint`
- Dockerfile: `hadolint`

## Pass/Fail

- PASS: `.coderabbit.yaml` exists and `auto_review` is enabled
- WARN: Missing `path_filters` or `instructions`
- FAIL: File missing or `auto_review` not configured
