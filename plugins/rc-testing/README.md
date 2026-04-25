# gangway-cli-4.22

RC candidate testing workflow for OCP 4.22 edge topologies (TNF, TNA, SNO 4vCPU).

Launches Prow CI jobs via [gangway-cli](https://github.com/openshift-eng/gangway-cli), tracks results, and reports status — per topology.

## Prerequisites

- `gangway-cli` binary built at `~/Projects/gangway-cli/gangway-cli`
- `MY_APPCI_TOKEN` environment variable set (get from [app.ci](https://console-openshift-console.apps.ci.l2s4.p1.openshiftapps.com))
- `jq`, `curl`, `python3`

## Quick start

```bash
# List available TNF jobs
./launch.sh tnf --list

# Refresh job list from Sippy
./launch.sh tnf --refresh

# Launch all TNF jobs against an RC
./launch.sh tnf 4.22.0-rc.0 --job all

# Launch specific jobs by number, list, or pattern
./launch.sh tnf 4.22.0-rc.0 --job 3
./launch.sh tnf 4.22.0-rc.0 --job 3,7,12
./launch.sh tnf 4.22.0-rc.0 --job recovery

# Launch TNA jobs (cross-upgrade jobs need --initial for the source version)
./launch.sh tna 4.22.0-rc.0 --job all --initial 4.21.0

# Preview without launching
./launch.sh tnf 4.22.0-rc.0 --job all --dry-run

# Check status
./status.sh tnf                    # Table view
./status.sh tnf --json             # Structured JSON
./status.sh tnf --failed --logs    # Failures with root cause
./status.sh tnf --report           # Jira-ready markdown
```

Version tags are expanded automatically: `4.22.0-rc.0` becomes `quay.io/openshift-release-dev/ocp-release:4.22.0-rc.0-x86_64`.

## Directory layout

```
gangway-cli-4.22/
├── jobs/
│   ├── tnf.txt          # TNF periodic jobs
│   ├── tna.txt          # TNA periodic jobs (cross-upgrade prefixed)
│   └── sno-4vcpu.txt    # SNO 4vCPU periodic jobs
├── launch.sh            # Unified launcher
├── status.sh            # Status checker, log fetcher, Jira reporter
└── runs/                # Tracking output (created at launch time)
    └── <date>/
        ├── config.env   # Release image, timestamp
        ├── tnf/         # One JSON per launched job
        ├── tna/
        └── sno-4vcpu/
```

## launch.sh

```
Usage: ./launch.sh <topology> <version> --job <selector> [options]
       ./launch.sh <topology> --list
       ./launch.sh <topology> --refresh
```

| Flag | Description |
|------|-------------|
| `<topology>` | `tnf`, `tna`, or `sno-4vcpu` |
| `<version>` | Version tag (e.g., `4.22.0-rc.0`) — not required for `--list` or `--refresh` |
| `--job <selector>` | **Required.** `all`, number (`3`), list (`3,7,12`), or pattern (`recovery`) |
| `--list` | List available jobs (numbered) and exit |
| `--refresh` | Update job file from Sippy and exit |
| `--initial <version>` | Set `RELEASE_IMAGE_INITIAL` for cross-upgrade jobs |
| `--run <name>` | Custom run directory name (defaults to `YYYY-MM-DD`) |
| `--dry-run` | Print what would be launched without calling gangway-cli |

### Pre-flight checks

Before launching, the script verifies:
1. `gangway-cli` binary exists and is executable (skipped for `--dry-run`)
2. Release image tag exists on quay.io (via REST API)
3. `MY_APPCI_TOKEN` is set and accepted by the Gangway API (skipped for `--dry-run`)

### Job files and Sippy refresh

Each `jobs/<topology>.txt` file lists one Prow job name per line. Use `--refresh` to update from Sippy:

```bash
./launch.sh tnf --refresh        # Fetches nightly jobs matching "two-node-fencing"
./launch.sh tna --refresh        # Fetches nightly jobs matching "two-node-arbiter"
./launch.sh sno-4vcpu --refresh  # Fetches nightly jobs matching "-4vcpu"
```

Cross-version upgrade jobs (those with `upgrade-from-stable` in the name) are automatically prefixed with `cross-upgrade:` during refresh. These jobs require `--initial` to set a different source version.

```
# Regular job — both --initial and --latest use the release image
periodic-ci-openshift-release-main-nightly-4.22-e2e-metal-ovn-two-node-fencing

# Cross-upgrade job — --initial uses the version from --initial flag
cross-upgrade:periodic-ci-openshift-release-main-nightly-4.22-upgrade-from-stable-4.21-e2e-metal-ovn-two-node-arbiter-upgrade
```

Within-version upgrade jobs (e.g., `arbiter-upgrade`, `fencing-upgrade`) do **not** get the prefix — CI resolves the upgrade path internally using the same image for both `--initial` and `--latest`.

Jobs are launched sequentially with a 10-second delay between each to avoid rate limiting.

## status.sh

```
Usage: ./status.sh [topology] [--run <name>] [--json] [--failed] [--logs] [--report]
```

| Flag | Description |
|------|-------------|
| `[topology]` | `tnf`, `tna`, or `sno-4vcpu` (omit for all topologies) |
| `--json` | Structured JSON output (for agentic consumption) |
| `--failed` | Show only failed/aborted jobs |
| `--logs` | Fetch failure reasons from Prow artifacts (`junit_operator.xml`) |
| `--report` | Jira-ready markdown output (implies `--logs`) |
| `--run <name>` | Use a specific run directory (defaults to latest) |

Exit code: `0` if all jobs passed or still running, `1` if any failed/aborted.

### Output modes

**Table** (default):
```
--- tnf ---
#   Status       Job                                                             URL
    -------------------------------------------------------------------------------------------------------------------
1   PASS         periodic-ci-...-two-node-fencing                                https://prow.ci...
2   FAIL         periodic-ci-...-two-node-fencing-recovery-1of3                  https://prow.ci...
                 → devscripts-setup: bootstrap process timed out
3   RUNNING      periodic-ci-...-two-node-fencing-degraded                       https://prow.ci...

    Total: 43 | Pass: 38 | Fail: 2 | Pending/Running: 3
```

**JSON** (`--json`):
```json
{
  "run": "2026-04-24",
  "release_image": "quay.io/...:4.22.0-rc.0-x86_64",
  "has_failures": true,
  "topologies": {
    "tnf": {
      "total": 43, "pass": 38, "fail": 2, "pending": 3,
      "jobs": [
        {"number": 2, "job": "periodic-ci-...", "status": "FAIL", "url": "...", "failure_reason": "devscripts-setup: bootstrap timed out"}
      ]
    }
  }
}
```

**Jira report** (`--report`):
```markdown
## RC Testing: 2026-04-24 — tnf

**Release**: `quay.io/...:4.22.0-rc.0-x86_64`
**Date**: 2026-04-24T10:30:00-04:00

| # | Result | Job | Notes |
|---|--------|-----|-------|
| 1 | PASS | [periodic-ci-...-fencing](https://prow.ci...) | |
| 2 | FAIL | [periodic-ci-...-recovery-1of3](https://prow.ci...) | devscripts-setup: bootstrap timed out |

**Summary**: 38/43 passed, 2 failed, 3 pending/running
```

Flags combine: `--report --failed` gives a Jira table of only failures.

## Jira tracking

| Topology | Ticket | Jobs |
|----------|--------|------|
| TNF | [OCPEDGE-2509](https://redhat.atlassian.net/browse/OCPEDGE-2509) | 43 |
| TNA | [OCPEDGE-2593](https://redhat.atlassian.net/browse/OCPEDGE-2593) | 14 |
| SNO 4vCPU | [OCPEDGE-2594](https://redhat.atlassian.net/browse/OCPEDGE-2594) | 4 |

## Agentic workflow

This directory is designed to be driven by Claude Code conversationally:

1. **Refresh**: "refresh TNF jobs" → `./launch.sh tnf --refresh`
2. **Launch**: "launch TNF against rc.0" → `./launch.sh tnf 4.22.0-rc.0 --job all`
3. **Monitor**: "check TNF status" → `./status.sh tnf --json` → parse and summarize
4. **Investigate**: "what failed?" → `./status.sh tnf --json --failed --logs` → failure reasons from Prow artifacts
5. **Report**: "update the Jira ticket" → `./status.sh tnf --report` → post to OCPEDGE-2509 via MCP
6. **Re-launch**: "re-launch the failures" → `./launch.sh tnf 4.22.0-rc.0 --job 5,12`

Each topology can be launched and tracked independently.
