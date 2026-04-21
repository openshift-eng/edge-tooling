# pr-monitor

Comment-driven PR lifecycle monitor -- invoke after creating a PR, and it
monitors for review comments (human and CodeRabbit), addresses inline
suggestions, investigates CI failures, pushes fixes, and loops until no new
comments appear and all CI jobs pass.

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

Invoke once after creating a PR. The plugin takes over:

1. Waits 2 minutes for reviewers to post initial comments
2. Fetches new review comments and CI job statuses (deterministic scripts)
3. Dispatches parallel analysis for comments (Track A) and CI failures (Track B)
4. Auto-pushes trivial fixes (style, naming, linting, imports, assertions)
5. Asks for confirmation on non-trivial changes (logic, API, structural)
6. Refuses security-sensitive changes (RBAC, credentials, dependencies)
7. Adapts wait time based on pending job types (5-30 min)
8. Loops until no new comments and all CI green

## Auto-Restart

If the session stops unexpectedly (Ctrl+C, context limit), a stop hook
evaluates whether the PR is fully resolved. If not, it restarts the monitor
automatically -- up to 3 times. State is carried via the `PR_MONITOR_STATE`
environment variable (no state files).

## Scripts

All GitHub/CI data gathering is handled by deterministic bash scripts:

| Script | Purpose |
|--------|---------|
| `pr-checks.sh` | Fetch PR metadata and Prow CI job statuses |
| `pr-comments.sh` | Fetch unresolved review comments (human + CodeRabbit) |
| `pr-state.sh` | Read/write monitor state via env var |
| `pr-push.sh` | Validate fork remote and push changes |
| `pr-stop-check.sh` | Evaluate restart conditions for stop hook |

## Requirements

- `gh` CLI installed and authenticated
- `jq` installed
- CI analysis plugins installed (`ci:prow-job-analyze-test-failure`, `ci:prow-job-analyze-install-failure`)

## Category

ci-cd

## Author

vimauro
