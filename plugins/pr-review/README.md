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

## Requirements

- **Claude Code:** >= 1.0.0
- **Category:** debug

## Author

fonta-rh
