# pr-monitor

Comment-driven PR lifecycle monitor -- invoke after creating a PR, and it
evaluates review comments (human and CodeRabbit), addresses inline
suggestions, investigates CI failures, and pushes fixes. Each invocation
performs one cycle, then the Stop hook reschedules the next cycle
automatically.

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

1. Fetches new review comments and CI job statuses (deterministic scripts)
2. Dispatches parallel analysis for comments (Track A) and CI failures (Track B)
3. Auto-pushes trivial fixes (style, naming, linting, imports, assertions)
4. Asks for confirmation on non-trivial changes (logic, API, structural)
5. Refuses security-sensitive changes (RBAC, credentials, dependencies)
6. Sets next check delay based on pending job types, then exits
7. Stop hook waits the delay and spawns a fresh session for the next cycle

Default: 3 iterations. Use `--infinite-loop` for unlimited:

```text
/pr-monitor:watch https://github.com/openshift/microshift/pull/6519 --infinite-loop
```

## Lifecycle

Each invocation performs exactly one cycle. The Stop hook handles continuation:

- `status=complete` → PR is done, no restart
- `status=waiting` → sleep `next_check_delay` seconds, then spawn new session
- `status=running` → unexpected exit (crash), retry immediately

State is carried via the `PR_MONITOR_STATE` environment variable. Each new
session starts with fresh context — no conversation history accumulation.

## Scripts

All GitHub/CI data gathering is handled by deterministic bash scripts:

| Script | Purpose |
|--------|---------|
| `pr-checks.sh` | Fetch PR metadata and CI job statuses |
| `pr-comments.sh` | Fetch unresolved inline review comments (human + CodeRabbit), filtering out resolved threads |
| `pr-state.sh` | Read/write monitor state via env var |
| `pr-push.sh` | Validate fork remote and push changes |
| `pr-stop-check.sh` | Evaluate continuation conditions and reschedule next cycle |

## Requirements

- `gh` CLI installed and authenticated
- `jq` installed
- CI analysis plugins installed (`ci:prow-job-analyze-test-failure`, `ci:prow-job-analyze-install-failure`)

## Category

ci-cd

## Author

vimauro
