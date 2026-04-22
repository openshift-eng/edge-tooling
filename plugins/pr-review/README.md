# pr-review

Post-review utilities for PR workflows. This plugin does **not** perform reviews — it provides follow-up tools for processing review output.

## Installation

Install via Claude Code's plugin system:

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install pr-review
```

## Skills

### vet-review

Skeptical second pass on findings from a prior review (e.g., `/review-pr`). Filters noise, validates real issues, and presents surviving findings one-by-one for accept/discard/discuss.

```text
# Run a review first, then vet the results
/review-pr 123
/pr-review:vet-review

# Or vet review comments already on a PR
/pr-review:vet-review 123
```

**This skill does not generate its own findings.** It works exclusively with output from a prior review. If no review findings are found, it will ask you to run `/review-pr` first.

### coderabbit

Triage CodeRabbit automated review comments on a PR. Fetches all inline CodeRabbit findings, vets them with vet-review's skepticism, categorizes into auto-apply/needs-review/dropped, and presents a single table for user confirmation before applying any changes. After confirmation, applies fixes in one commit and replies to each CodeRabbit comment on the PR.

```text
# Process CodeRabbit comments on a specific PR
/pr-review:coderabbit 123

# From a PR URL
/pr-review:coderabbit https://github.com/org/repo/pull/123

# Cross-repo
/pr-review:coderabbit org/repo#123

# Auto-detect PR from current branch
/pr-review:coderabbit
```

**This skill does not generate its own findings.** It works exclusively with CodeRabbit's inline review comments. Summary/walkthrough comments are read for context but not actioned.

## Requirements

- **Claude Code:** >= 1.0.0
- **Category:** debug

## Author

fonta-rh
