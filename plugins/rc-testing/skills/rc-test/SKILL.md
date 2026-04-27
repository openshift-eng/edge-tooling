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
- `--initial` is needed for TNA cross-upgrade jobs (e.g., `--initial 4.21.0`)
- Always confirm with the user before launching without `--dry-run`

**Example**: "launch TNF against rc.0" becomes:

```bash
bash ${CLAUDE_SKILL_DIR}/../../scripts/launch.sh tnf 4.22.0-rc.0 --job all
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

1. Run `status.sh <topology> --json --failed --logs` to get failures with reasons
2. Summarize each failure: job name, job number, and root cause
3. Offer next steps: "Want me to re-launch these, or update the Jira ticket?"

## Workflow

The typical flow for an RC test cycle:

1. **Refresh** job lists from Sippy (if needed)
2. **Launch** jobs against the RC build
3. **Monitor** status periodically until all jobs complete
4. **Investigate** any failures
5. **Report** results to Jira
6. **Re-launch** failed jobs if they were infra failures

## Important Notes

- The `MY_APPCI_TOKEN` environment variable must be set before launching (not needed for status/list/refresh)
- Version tags are short form: `4.22.0-rc.0` (auto-expanded to full quay.io URL)
- Exit code from status.sh: 0 = all pass or running, 1 = any failures
- Cross-upgrade jobs (TNA only) require `--initial` to specify the source version
- When re-launching failed jobs, use their job numbers from the status output with `--job`
