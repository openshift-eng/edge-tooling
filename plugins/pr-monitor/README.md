# pr-monitor

Autonomous PR CI lifecycle manager -- invoke once, and it monitors Prow jobs, analyzes
failures, fixes issues, addresses PR review comments, and loops until the PR is green.

## Installation

Install via Claude Code's plugin system:

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install pr-monitor
```

## Usage

```text
/pr-monitor:watch https://github.com/openshift/microshift/pull/6519
```

Invoke once. The plugin takes over: it polls CI jobs every 3-5 minutes, analyzes
failures via CI skills, proposes and pushes fixes (with your confirmation),
addresses PR review comments, retriggers failed jobs, and loops until all CI
jobs pass and all review feedback is resolved. You do not need to run anything
else -- just approve or decline when prompted.

## How It Works

1. Extracts PR metadata and CI job statuses via `gh` CLI
2. Filters for Prow CI jobs (ignores GitHub Actions and other CI systems)
3. Routes failed jobs to appropriate analysis skills based on job type
4. Classifies failures as infrastructure (non-actionable) or code (actionable)
5. For code failures: proposes fixes with explicit user confirmation before push
6. Retriggers failed jobs via `/retest` comment (with user confirmation)
7. Fetches PR review comments and addresses actionable reviewer feedback
8. Loops continuously until all CI passes and reviews are resolved

## Safety Features

- User confirmation required before any git push or job retrigger
- Fork remote validation ensures pushes go to your fork, not upstream
- Organization allowlist restricts auto-push to trusted repos (openshift, openshift-eng)
- Max 2 fix iterations per monitoring session
- Security-sensitive files are excluded from auto-fix proposals

## Requirements

- `gh` CLI installed and authenticated
- `jq` installed
- CI analysis plugins installed (`ci:prow-job-analyze-test-failure`, `ci:prow-job-analyze-install-failure`)

## Category

ci-cd

## Author

vimauro
