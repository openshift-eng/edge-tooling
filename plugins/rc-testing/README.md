# rc-testing

Release candidate testing plugin for OCP edge topologies (TNF, TNA, SNO).

Launches Prow CI jobs via [gangway-cli](https://github.com/openshift-eng/gangway-cli), tracks results, investigates failures, and reports to Jira — per topology.

## Setup

### 1. Build gangway-cli

[gangway-cli](https://github.com/openshift-eng/gangway-cli) is a Go CLI that submits jobs to the Prow Gangway API. It takes `--initial` and `--latest` release images, a `--job-name`, and writes tracking JSONs that `status.sh` reads.

```bash
git clone git@github.com:openshift-eng/gangway-cli.git ~/Projects/gangway-cli
cd ~/Projects/gangway-cli
go build .    # produces ./gangway-cli binary
```

Requires Go 1.20+.

### 2. Get your app.ci token

1. Log in to [app.ci console](https://console-openshift-console.apps.ci.l2s4.p1.openshiftapps.com)
2. Click your username (top right) → **Copy login command** → **Display Token**
3. Copy the token and export it:

```bash
export MY_APPCI_TOKEN="sha256~..."
```

### 3. System dependencies

- `jq` — JSON parsing (status output, quay.io tag verification)
- `curl` — API calls (Gangway, quay.io, GCS artifacts)
- `python3` — Sippy URL encoding, junit XML parsing for `--logs`

## Quick start

```bash
# List available TNF jobs
scripts/launch.sh tnf --list

# Refresh job list from Sippy
scripts/launch.sh tnf --refresh

# Launch all TNF jobs against an RC
scripts/launch.sh tnf 4.22.0-rc.0 --job all

# Launch specific jobs by number, list, or pattern
scripts/launch.sh tnf 4.22.0-rc.0 --job 3
scripts/launch.sh tnf 4.22.0-rc.0 --job 3,7,12
scripts/launch.sh tnf 4.22.0-rc.0 --job recovery

# Launch TNA jobs (cross-upgrade jobs need --initial for the source version)
scripts/launch.sh tna 4.22.0-rc.0 --job all --initial 4.21.0

# Preview without launching
scripts/launch.sh tnf 4.22.0-rc.0 --job all --dry-run

# Re-launch failed jobs from the latest run
scripts/launch.sh tnf 4.22.0-rc.1 --relaunch-failed

# Check status
scripts/status.sh tnf                    # Table view
scripts/status.sh tnf --json             # Structured JSON
scripts/status.sh tnf --failed --logs    # Failures with root cause
scripts/status.sh tnf --failed --classify # Failures classified against nightly history
scripts/status.sh tnf --report           # Jira-ready markdown
scripts/status.sh tnf --watch            # Poll every 120s until all jobs complete
scripts/status.sh tnf --watch 60         # Poll every 60s
```

Version tags are expanded automatically: `4.22.0-rc.0` becomes `quay.io/openshift-release-dev/ocp-release:4.22.0-rc.0-x86_64`.

## Directory layout

```text
rc-testing/
├── jobs/
│   ├── tnf.txt              # TNF periodic jobs
│   ├── tna.txt              # TNA periodic jobs (cross-upgrade prefixed)
│   └── sno.txt        # SNO periodic jobs
├── scripts/
│   ├── launch.sh            # Unified launcher (wraps gangway-cli)
│   └── status.sh            # Status, logs, and Jira reporting
├── skills/
│   └── rc-test/
│       └── SKILL.md         # Claude Code skill definition
├── runs/                    # Tracking output (created at launch time)
│   └── <date>/
│       ├── tnf/             # One JSON per launched job + config.env
│       ├── tna/
│       └── sno/
└── README.md
```

## launch.sh

```text
Usage: scripts/launch.sh <topology> <version> --job <selector> [options]
       scripts/launch.sh <topology> --list
       scripts/launch.sh <topology> --refresh
```

| Flag | Description |
|------|-------------|
| `<topology>` | `tnf`, `tna`, or `sno` |
| `<version>` | Version tag (e.g., `4.22.0-rc.0`) — not required for `--list` or `--refresh` |
| `--job <selector>` | **Required** (unless `--relaunch-failed`). `all`, number (`3`), list (`3,7,12`), or pattern (`recovery`) |
| `--relaunch-failed` | Re-launch failed jobs from the latest run |
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
scripts/launch.sh tnf --refresh        # Fetches nightly jobs matching "two-node-fencing"
scripts/launch.sh tna --refresh        # Fetches nightly jobs matching "two-node-arbiter"
```

Cross-version upgrade jobs (those with `upgrade-from-stable` in the name) are automatically prefixed with `cross-upgrade:` during refresh. These jobs require `--initial` to set a different source version.

```text
# Regular job — both --initial and --latest use the release image
periodic-ci-openshift-release-main-nightly-4.22-e2e-metal-ovn-two-node-fencing

# Cross-upgrade job — --initial uses the version from --initial flag
cross-upgrade:periodic-ci-openshift-release-main-nightly-4.22-upgrade-from-stable-4.21-e2e-metal-ovn-two-node-arbiter-upgrade
```

Within-version upgrade jobs (e.g., `arbiter-upgrade`, `fencing-upgrade`) do **not** get the prefix — CI resolves the upgrade path internally using the same image for both `--initial` and `--latest`.

Jobs are launched sequentially with a 10-second delay between each to avoid rate limiting.

## status.sh

```text
Usage: scripts/status.sh [topology] [--run <name>] [--json] [--failed] [--logs] [--report]
```

| Flag | Description |
|------|-------------|
| `[topology]` | `tnf`, `tna`, or `sno` (omit for all topologies) |
| `--json` | Structured JSON output (for agentic consumption) |
| `--failed` | Show only failed/aborted jobs |
| `--logs` | Fetch failure reasons from Prow artifacts (`junit_operator.xml`) |
| `--classify` | Classify failures using Sippy nightly pass rates (implies `--logs`) |
| `--report` | Jira-ready markdown output (implies `--logs`) |
| `--watch [N]` | Poll every N seconds (default 120) until all jobs complete |
| `--run <name>` | Use a specific run directory (defaults to latest) |

Exit code: `0` if all jobs passed or still running, `1` if any failed/aborted.

### Output modes

**Table** (default):

```text
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

### Failure classification (`--classify`)

The `--classify` flag queries Sippy nightly pass rates for each failed job and tags it:

| Pass Rate | Classification | Meaning |
|-----------|---------------|---------|
| No data | `NO-DATA` | Job not in Sippy (new job?) |
| 0% | `KNOWN-FAIL` | Always failing in nightly — pre-existing |
| < 50% | `FLAKY` | Fails more than half the time — noise |
| 50-85% | `SOMETIMES-FAILS` | Intermittent, may or may not be RC-related |
| >= 85% | `REGRESSION` | Usually passes but failed on RC — investigate |

Works with all output modes: `--classify`, `--json --classify`, `--report --classify`.

## Jira tracking

| Topology | Ticket | Jobs |
|----------|--------|------|
| TNF | [OCPEDGE-2509](https://redhat.atlassian.net/browse/OCPEDGE-2509) | 43 |
| TNA | [OCPEDGE-2593](https://redhat.atlassian.net/browse/OCPEDGE-2593) | 14 |
| SNO | [OCPEDGE-2594](https://redhat.atlassian.net/browse/OCPEDGE-2594) | 4 |

## Claude Code skill

The `/rc-test` skill enables conversational testing:

```text
/rc-test launch tnf 4.22.0-rc.0
/rc-test status tnf
/rc-test report tna
```

Or just speak naturally — "launch TNF against rc.0", "check status", "what failed?", "update the Jira ticket".

### Agentic workflow

1. **Refresh**: "refresh TNF jobs" → updates job list from Sippy
2. **Launch**: "launch TNF against rc.0" → runs all 43 jobs via gangway-cli
3. **Monitor**: "check TNF status" → parses JSON, summarizes pass/fail
4. **Investigate**: "what failed?" → classifies failures against Sippy nightly history, groups by severity (regression vs known-fail vs flaky), recommends what to re-launch vs ignore
5. **Report**: "update the Jira ticket" → generates markdown, posts to OCPEDGE ticket via MCP
6. **Re-launch**: "re-launch the failures" → re-runs just the failed job numbers

Each topology can be launched and tracked independently.
