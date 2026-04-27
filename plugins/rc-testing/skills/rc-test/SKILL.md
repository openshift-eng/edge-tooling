---
name: rc-test
description: Release candidate testing for OCP edge topologies (TNF, TNA, SNO). Launch Prow CI jobs, monitor status, investigate failures, and report results to Jira.
allowed-tools: Bash(bash *) Bash(cd *) Read
arguments: [action]
argument-hint: <action> [args...] — actions: launch, status, list, refresh, report, investigate
---

# Release Candidate Testing

You are orchestrating release candidate testing for OCP edge topologies. The scripts are at `${CLAUDE_SKILL_DIR}/../../scripts/`.

## Available Actions

Parse `$ARGUMENTS` to determine the action and arguments. The user may phrase requests naturally — map their intent to the appropriate action below.

### list — Show available jobs

**Triggers**: "list", "show jobs", "what jobs"

```bash
bash ${CLAUDE_SKILL_DIR}/../../scripts/launch.sh <topology> --list
```

Topologies: `tnf`, `tna`, `sno`

### refresh — Update job list from Sippy

**Triggers**: "refresh", "update jobs", "sync from sippy"

```bash
bash ${CLAUDE_SKILL_DIR}/../../scripts/launch.sh <topology> --refresh
```

### launch — Launch Prow CI jobs

**Triggers**: "launch", "run", "start", "test"

```bash
bash ${CLAUDE_SKILL_DIR}/../../scripts/launch.sh <topology> <version> --job <selector> [--initial <version>] [--dry-run]
```

- `--job` is required: `all`, a number (`3`), a list (`3,7,12`), or a pattern (`recovery`)
- `--relaunch-failed` re-launches failed jobs from the latest run (no `--job` needed)
- `--initial` is needed for TNA cross-upgrade jobs (e.g., `--initial 4.21.0`)
- Always confirm with the user before launching without `--dry-run`

**Example**: "launch TNF against rc.0" becomes:

```bash
bash ${CLAUDE_SKILL_DIR}/../../scripts/launch.sh tnf 4.22.0-rc.0 --job all
```

**Example**: "re-launch the failures" becomes:

```bash
bash ${CLAUDE_SKILL_DIR}/../../scripts/launch.sh <topology> <version> --relaunch-failed
```

### status — Check job results

**Triggers**: "status", "check", "how are the jobs", "results"

For your own analysis, use JSON mode:

```bash
bash ${CLAUDE_SKILL_DIR}/../../scripts/status.sh <topology> --json [--failed] [--logs]
```

For showing the user a table:

```bash
bash ${CLAUDE_SKILL_DIR}/../../scripts/status.sh <topology> [--failed] [--logs]
```

Key flags:

- `--json` — structured output you can parse programmatically
- `--failed` — only show failed/aborted jobs
- `--logs` — fetch failure reasons from Prow artifacts (junit_operator.xml)

After checking status, summarize: how many passed, how many failed, how many still running. If there are failures and `--logs` was used, include the failure reason for each.

To watch until all jobs complete:

```bash
bash ${CLAUDE_SKILL_DIR}/../../scripts/status.sh <topology> --watch [interval]
```

### report — Generate Jira-ready output

**Triggers**: "report", "update jira", "post to jira", "update the ticket"

```bash
bash ${CLAUDE_SKILL_DIR}/../../scripts/status.sh <topology> --report [--failed]
```

This outputs Jira-ready markdown. Post it to the appropriate ticket using the Jira MCP tool:

| Topology | Jira Ticket |
|----------|-------------|
| TNF | OCPEDGE-2509 |
| TNA | OCPEDGE-2593 |
| SNO | OCPEDGE-2594 |

### investigate — Dig into failures

**Triggers**: "what failed", "investigate", "why did it fail"

```bash
bash ${CLAUDE_SKILL_DIR}/../../scripts/status.sh <topology> --json --failed --classify
```

1. Run `status.sh <topology> --json --failed --classify` to get failures with classification
2. Group findings by classification:
   - **REGRESSION** (>= 85% nightly pass rate): Needs investigation — usually passes but failed on RC
   - **SOMETIMES-FAILS** (50-85%): Intermittent — may or may not be RC-related
   - **FLAKY** (< 50%): Fails often in nightly — likely noise, not RC-specific
   - **KNOWN-FAIL** (0%): Always failing — pre-existing, don't block RC
   - **NO-DATA**: New job not yet tracked by Sippy — check manually
3. **Cross-topology correlation**: If multiple topologies were tested, run `status.sh --json --failed --classify` (no topology filter) and look for patterns:
   - Same failure reason across topologies → infra issue, not topology-specific
   - Same job step failing everywhere (e.g., all `devscripts-setup` failures) → environment problem
   - Failure only in one topology → likely topology-specific, worth investigating
4. For REGRESSION failures, investigate root cause using the failure_reason
5. Offer next steps: "Want me to re-launch the regressions, or update the Jira ticket?"

## Workflow

The typical flow for an RC test cycle:

1. **Refresh** job lists from Sippy (if needed)
2. **Launch** jobs against the RC build
3. **Monitor** status with `--watch` until all jobs complete
4. **Investigate** failures with `--classify` for cross-topology patterns
5. **Report** results to Jira
6. **Re-launch** failed jobs with `--relaunch-failed`

## Important Notes

- The `MY_APPCI_TOKEN` environment variable must be set before launching (not needed for status/list/refresh)
- Version tags are short form: `4.22.0-rc.0` (auto-expanded to full quay.io URL)
- Exit code from status.sh: 0 = all pass or running, 1 = any failures
- Cross-upgrade jobs (TNA only) require `--initial` to specify the source version
- Use `--relaunch-failed` to re-launch failed jobs, or `--job <numbers>` for specific jobs
