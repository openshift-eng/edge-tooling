# pr-review

PR lifecycle toolkit — post-review utilities and autonomous PR monitoring. Provides tools for processing review output and an autonomous agent that monitors CI, triages comments, and auto-fixes issues.

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

# Batch mode — output structured JSON instead of interactive flow
# (used by yolo-agent for automated integration)
/pr-review:vet-review 123 --batch
```

**This skill does not generate its own findings.** It works exclusively with output from a prior review. If no review findings are found, it will ask you to run `/review-pr` first.

**Batch mode (`--batch`):** Performs identical vetting but outputs categorized findings as JSON instead of presenting them interactively. Does not apply changes or prompt for confirmation. Used by yolo-agent for standardized comment analysis.

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

# Batch mode — output structured JSON instead of interactive flow
# (used by yolo-agent for automated integration)
/pr-review:coderabbit 123 --batch
```

**This skill does not generate its own findings.** It works exclusively with CodeRabbit's inline review comments. Summary/walkthrough comments are read for context but not actioned.

**Batch mode (`--batch`):** Performs identical vetting but outputs categorized findings as JSON instead of presenting the interactive table. Does not apply changes, reply to comments, or prompt for confirmation. Used by yolo-agent for standardized comment analysis.

### yolo-agent

Autonomous PR lifecycle agent. Monitors CI checks and review comments, auto-fixes trivial issues (style, linting, imports), asks for confirmation on non-trivial changes, and loops until the PR is ready. Uses `CronCreate` to automatically schedule the next cycle within the same session.

Comment analysis is routed through the team's standardized skills: bot comments (e.g., CodeRabbit) are analyzed via the `coderabbit` skill in batch mode, and human comments are analyzed via the `vet-review` skill in batch mode. This ensures consistent vetting criteria across all invocation paths.

```text
# Monitor a PR (default 3 iterations)
/pr-review:yolo-agent https://github.com/org/repo/pull/123

# Monitor with unlimited iterations
/pr-review:yolo-agent https://github.com/org/repo/pull/123 --infinite-loop

# Only process bot comments (skip human reviewers)
/pr-review:yolo-agent https://github.com/org/repo/pull/123 --skip-users

# Yolo mode — auto-push all changes without confirmation (security checks still apply)
/pr-review:yolo-agent https://github.com/org/repo/pull/123 --yolo

# Combine flags
/pr-review:yolo-agent https://github.com/org/repo/pull/123 --infinite-loop --skip-users --yolo
```

**Auto-push rules:** Trivial changes (style, naming, linting, imports, simple assertions) are pushed without confirmation. Non-trivial changes require explicit approval unless `--yolo` is active, which auto-pushes all changes. Security-sensitive files are never modified regardless of flags.

## Future Work

- **Context isolation per cycle**: Interactive mode currently uses CronCreate
  to schedule the next cycle within the same session, causing conversation
  context to accumulate across cycles. All cycle state is already externalized
  to a JSON file, making conversation history redundant. A session-per-cycle
  architecture (spawning a fresh `claude -p` session for each cycle, like
  headless mode already does) would give every cycle a clean context window.
  Tradeoff: non-yolo interactive mode would lose mid-loop confirmation of
  non-trivial changes — these would need to be deferred and reported instead.

## Requirements

- **Claude Code:** >= 1.0.0
- **Category:** debug

## Authors

fonta-rh, vmauro
