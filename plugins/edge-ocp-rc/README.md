# edge-ocp-rc

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

The scripts find `gangway-cli` via `GANGWAY_BIN`. If the binary is on your `PATH`, it's detected automatically. Otherwise, export it:

```bash
export GANGWAY_BIN=~/Projects/gangway-cli/gangway-cli
```

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
# Fetch all job lists from Sippy (TNF + TNA × 4.22, 4.23, 5.0)
scripts/collect.sh

# List available TNF jobs (all releases)
scripts/launch.sh tnf --list

# Refresh a single topology from Sippy
scripts/launch.sh tnf 5.0.0 --refresh

# Launch regular TNF jobs against an RC
scripts/launch.sh tnf 4.22.0-rc.0 --job all

# Launch ALL jobs including upgrades (--initial enables upgrade jobs)
scripts/launch.sh tna 4.22.0-rc.0 --initial 4.21.0 --job all

# Launch specific jobs by number, list, or pattern
scripts/launch.sh tnf 4.22.0-rc.0 --job 3
scripts/launch.sh tnf 4.22.0-rc.0 --job 3,7,12
scripts/launch.sh tnf 4.22.0-rc.0 --job recovery

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
edge-ocp-rc/
├── jobs/
│   └── <release>/               # e.g., 4.22/, 4.23/, 5.0/
│       ├── tnf.txt              # Regular TNF jobs
│       ├── tnf-z-stream.txt     # TNF z-stream upgrade jobs
│       ├── tnf-y-stream.txt     # TNF y-stream upgrade jobs
│       ├── tna.txt              # Regular TNA jobs
│       ├── tna-z-stream.txt     # TNA z-stream upgrade jobs
│       └── tna-y-stream.txt     # TNA y-stream upgrade jobs
├── scripts/
│   ├── collect.sh           # Fetch all topologies × releases from Sippy
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
| `--refresh` | Update job files from Sippy and exit |
| `--initial <version>` | Set `RELEASE_IMAGE_INITIAL` — required to include z-stream and y-stream upgrade jobs |
| `--run <name>` | Custom run directory name (defaults to `YYYY-MM-DD`) |
| `--dry-run` | Print what would be launched without calling gangway-cli |

### Pre-flight checks

Before launching, the script verifies:

1. `gangway-cli` is a regular file and executable (skipped for `--dry-run`, `--list`, `--refresh`)
2. Release image tag exists on quay.io (via REST API)
3. `MY_APPCI_TOKEN` is set and accepted by the Gangway API (skipped for `--dry-run`)

### Job files and Sippy refresh

Job files are organized by release under `jobs/<release>/`. Each topology has up to three files:

| File | Type | Description |
|------|------|-------------|
| `<topology>.txt` | Regular | Standard CI jobs — no upgrade path |
| `<topology>-z-stream.txt` | z-stream | Within-version upgrades (e.g., 4.22.0-ec.4 → 4.22.0-rc.0) |
| `<topology>-y-stream.txt` | y-stream | Cross-version upgrades (e.g., 4.21.0 → 4.22.0-rc.0) |

Each file is a plain list of Prow job names, one per line. No prefixes.

Use `collect.sh` to fetch all topologies and releases at once:

```bash
scripts/collect.sh                     # Fetches TNF + TNA for 4.22, 4.23, 5.0
```

Or refresh a single topology with `--refresh`:

```bash
scripts/launch.sh tnf 5.0.0 --refresh  # Fetches nightly jobs matching "two-node-fencing" for 5.0
scripts/launch.sh tna 4.22.0 --refresh  # Fetches nightly jobs matching "two-node-arbiter" for 4.22
```

Jobs are sorted into files automatically:

- Names containing `upgrade-from-stable` go to the y-stream file
- Names ending with `-upgrade` go to the z-stream file
- Everything else goes to the regular file

### Upgrade jobs and --initial

Without `--initial`, only regular jobs are launched. Upgrade jobs are skipped with a summary message.

With `--initial`, all three files are processed:

```bash
# Regular jobs only
scripts/launch.sh tnf 4.22.0-rc.0 --job all

# All jobs including upgrades
scripts/launch.sh tna 4.22.0-rc.0 --initial 4.21.0 --job all
```

Jobs are launched sequentially with a 10-second delay between each to avoid rate limiting. Override with `DELAY=30 scripts/launch.sh ...` if Gangway is throttling.

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

Use `--report` output as a Jira comment on your team's RC testing ticket for the current release cycle.

## Claude Code skill

The `/rc-test` skill enables conversational testing:

```text
/rc-test launch tnf 4.22.0-rc.0
/rc-test status tnf
/rc-test report tna
```

Or just speak naturally — "launch TNF against rc.0", "check status", "what failed?", "update the Jira ticket".

### Agentic workflow

1. **Refresh**: "refresh TNF jobs" → updates job lists from Sippy (regular, z-stream, y-stream)
2. **Launch**: "launch TNF against rc.0" → runs regular jobs via gangway-cli
3. **Launch upgrades**: "launch with initial 4.21.0" → includes z-stream and y-stream upgrade jobs
4. **Monitor**: "check TNF status" → parses JSON, summarizes pass/fail
5. **Investigate**: "what failed?" → classifies failures against Sippy nightly history, groups by severity (regression vs known-fail vs flaky), recommends what to re-launch vs ignore
6. **Report**: "update the Jira ticket" → generates markdown, posts to Jira via MCP
7. **Re-launch**: "re-launch the failures" → re-runs just the failed job numbers

Each topology can be launched and tracked independently.
