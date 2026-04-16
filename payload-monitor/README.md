# Edge OCP Payload Monitor

Automated monitoring tool for OpenShift nightly payload health across edge topologies (SNO, TNA, TNF). Fetches data from the amd64 release controller, Sippy Component Readiness, Prow CI, and JIRA to produce an interactive HTML dashboard.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run with defaults (monitors versions 4.18 through 5.0)
python -m payload_monitor

# Run and open report in browser
python -m payload_monitor --open

# Override versions
python -m payload_monitor --versions 4.18,4.19,4.20,4.21,4.22,4.23

# Patch AI analysis into an existing HTML report (the HTML file must already exist)
python -m payload_monitor --merge-analysis reports/analysis-2026-03-25.json --output reports/report-2026-03-25.html
```

## What It Does

1. **Fetches nightly payloads** from the [amd64 release controller](https://amd64.ocp.releases.ci.openshift.org) for OCP nightly streams (4.18 through 5.0 by default, overridable with `--versions`)
2. **Filters for edge topology jobs** (SNO, TNA, TNF) in blocking and informing job results
3. **Analyzes failures** by fetching Prow job logs and extracting failing test names and error signatures
4. **Queries Sippy** for job-level regressions (pass rate drops) across edge topologies
5. **Queries Component Readiness** for statistically significant regressions on edge topologies (SNO, TNF) vs HA
6. **Collects timing insights** (opt-in via `--with-timing`) for install/upgrade durations across SNO/TNA/TNF topologies
7. **Searches JIRA** for existing bugs matching failure signatures
8. **Generates an HTML dashboard** with health summaries, failure details, regressions, timing insights, and JIRA integration

## Architecture

```text
                       +-------------------+
                       |   CLI / Skill     |
                       +--------+----------+
                                |
     +----------+----------+----+-----+----------+----------+
     |          |          |          |          |           |
+----v-----+ +-v------+ +-v------+ +-v--------+ +v--------+ +v---------+
| Release  | | Sippy  | | Comp.  | | Timing   | | Prow    | | JIRA     |
| Ctrl     | | Jobs   | | Ready. | | (opt-in) | | Collect.| | Collect. |
+----+-----+ +--------+ +--------+ +----+-----+ +----+----+ +----------+
     |                                   |            |           |
     +---------------+------------------+-------------+-----------+
                      |
             +--------v----------+
             |    Analyzer       |
             | (recurring fails, |
             |  unstable jobs,   |
             |  cross-topology,  |
             |  JIRA matching)   |
             +--------+----------+
                      |
             +--------v----------+
             |  HTML Dashboard   |
             +-------------------+
```

### Usage

**Standalone CLI**: Run `python -m payload_monitor` to collect data and generate an HTML dashboard.

**Claude Code skill**: Run `/edge-ocp-ci:generate-dashboard` to also get AI-powered root cause analysis for blocking job failures, using marketplace CI skills from [ai-helpers](https://github.com/openshift-eng/ai-helpers).

### Performance and Token Efficiency

- **Parallel data collection**: All data sources are queried concurrently using `ThreadPoolExecutor`:
  - Release Controller, Sippy, and Component Readiness APIs run in parallel (`__main__.py`)
  - Per-stream payload tag detail fetches run concurrently within each stream (`collectors/release_controller.py`)
  - Prow artifact enrichment (junit XML downloads) runs across failing jobs in parallel (`collectors/prow.py`)
  - JIRA bug searches run concurrently across all unique failing jobs (`collectors/jira.py`)
- **Shared HTTP sessions**: All collectors reuse persistent `requests.Session` instances with automatic retry (3 attempts) and exponential backoff for transient failures (429, 5xx).
- **No JSON round-trip for AI analysis**: AI analysis is patched directly into the existing HTML report instead of serializing/deserializing the full report data through JSON. The `--json` export preserves all enrichment data (failure counts, JIRA matches, escalation risks, cross-topology correlations, thresholds) for external consumption.
- **Minimal AI input**: Blocking job data is emitted to stdout via structured markers (`BLOCKING_JOBS_START`/`BLOCKING_JOBS_END`) — Claude reads only job names and Prow URLs, never the full report data. Deep analysis runs only on blocking job failures; informing jobs get a lightweight Claude suggestion instead.
- **Multi-agent parallelism**: When multiple blocking jobs need analysis, each is analyzed by a separate subagent in parallel.

## Configuration

Configuration is hardcoded in `payload_monitor/config.py`. The defaults are:

| Setting | Default | Override |
|---------|---------|----------|
| Versions | 4.18, 4.19, 4.20, 4.21, 4.22, 4.23, 5.0 | `--versions` CLI flag |
| Payloads per stream | 6 | — |
| JIRA project | OCPBUGS | — |
| Report directory | `./reports` | `--output` CLI flag |
| Recurring threshold | 2 payloads | — |
| Persistent threshold | 3 payloads | — |
| Escalation threshold | 3 consecutive | — |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `JIRA_TOKEN` | For JIRA features | Atlassian Cloud Personal Access Token (read-only) |

To obtain a token, go to [Atlassian API Tokens](https://id.atlassian.com/manage-profile/security/api-tokens) and create a new token. **Use read-only permissions** — this tool only searches for existing bugs and never creates or modifies JIRA issues. The "Create in JIRA" links open the browser for manual review before submission.

Set it in your shell before running:

```bash
export JIRA_TOKEN="your-pat-here"
```

Or add it to `~/.bashrc` / `~/.zshrc` for persistence.

## CLI Reference

```text
Usage: python -m payload_monitor [OPTIONS]

Options:
  --versions TEXT      Override versions, comma-separated (e.g., "4.18,4.19")
  --output PATH        Output HTML file path (default: reports/report-YYYY-MM-DD.html)
  --from-json PATH     Regenerate HTML from a JSON file (skips data collection)
  --json               Also export full report data as JSON
  --merge-analysis PATH  Patch analysis JSON into an existing HTML report (or into --from-json data)
  --open               Open report in browser after generation
  --skip-prow          Skip Prow artifact fetching (faster, less detail)
  --skip-sippy         Skip Sippy regression check
  --with-timing        Include install/upgrade timing insights (disabled by default)
  --verbose            Enable verbose logging
  --help               Show this message and exit
```

## Data Sources

| Source | API | Auth | Purpose |
|--------|-----|------|---------|
| [Release Controller](https://amd64.ocp.releases.ci.openshift.org) | `/api/v1/releasestream/*/tags` | None | Payload status, blocking/informing job results |
| [Sippy](https://sippy.dptools.openshift.org) | `/api/jobs` | None | Job pass rates, regressions |
| [Sippy Component Readiness](https://sippy.dptools.openshift.org/sippy-ng/component_readiness/main) | `/api/component_readiness` | None | HA vs edge topology (SNO, TNF) regression detection |
| [Prow](https://prow.ci.openshift.org) | GCS artifacts via `gsutil` | Google Cloud SDK | Job logs, junit XMLs, failing test details |
| [JIRA](https://redhat.atlassian.net) | REST API v3 | Token (read-only) | Existing bug search, bug creation links |

## Topology Job Patterns

Jobs are classified by topology based on name patterns (case-insensitive matching):

- **SNO** (Single Node OpenShift): `sno`, `single-node`, `metal-single-node`
- **TNA** (Two Node with Arbiter): `tna`, `arbiter`
- **TNF** (Two Node Fencing): `tnf`, `fencing`

Jobs containing `telco` in the name are excluded from all topologies to avoid false matches.

These patterns are defined in `payload_monitor/config.py`.

## Dashboard Features

The generated HTML report is a single self-contained file (no external dependencies) with:

- **Health overview**: Per-version status badges, blocking/informing counts, topology badges, trend indicators, and payload acceptance timeline
- **Findings summary**: Situation bar (blocking, informing, regressions, affected topologies) with severity-tiered sections (Critical/Warning/Regressions), inline blocking job list, unstable job details, and prominent action buttons
- **Failing edge jobs**: Blocking and informing job failures across SNO/TNA/TNF topologies with sortable, filterable tables
- **Failure analysis**: Error messages, failing tests, and AI root cause analysis (when enriched via Claude skill)
- **Sippy job regressions**: Edge jobs with significant pass rate drops compared to previous periods, with an **Action** column containing copyable `/ci:triage-regression` commands
- **Component Readiness**: HA vs edge topology (SNO, TNF) regressions detected by Fisher's exact test, with comparison filter and **Action** column for triage commands
- **Timing insights** (opt-in): Install/upgrade duration stats, variant breakdowns, and phase duration trends per topology
- **JIRA integration**: Matching existing bugs and suggested new bugs with pre-filled create links

### Failure Intelligence

The analyzer automatically detects patterns across payloads to surface high-priority issues:

- **Recurring failures** (2+ payloads): Jobs that fail in multiple payloads are badged as "Recurring (Nx)" — likely not flakes
- **Persistent failures** (3+ payloads): Jobs failing across 3 or more payloads are badged as "Persistent (Nx)" and highlighted in the findings summary
- **Unstable jobs**: Informing jobs with 3+ **consecutive** recent failures are flagged as "Unstable" — these are consistently failing and need attention
- **Cross-topology correlation**: When the same base job fails across multiple topologies (e.g., SNO and TNA), each failure shows an "Also in: [topology]" hint to surface shared platform issues
- **Inline JIRA matches**: Each failing job's detail section shows any matching JIRA bugs inline, or a "Create Bug in JIRA" button if no existing bug is found (requires `JIRA_TOKEN`)
- **JIRA error surfacing**: When individual JIRA searches fail (network errors, auth issues), the dashboard displays a warning banner listing affected jobs — partial JIRA results are still shown for jobs that succeeded
- **Non-fatal analysis**: If the analyzer or any enrichment step fails, the report is still generated with the data collected so far — errors are logged and surfaced in the dashboard rather than aborting the entire run
- **No dead ends**: Every finding in the dashboard has a clear next step — a JIRA link, a copyable Claude command, a triage URL, or a bug creation button

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run with verbose output
python -m payload_monitor --verbose
```

### Running Tests

Tests use [pytest](https://docs.pytest.org/) and live in the `tests/` directory. All external HTTP calls are mocked, so no network access or credentials are needed.

```bash
# Install test dependencies
pip install pytest

# Run all tests
python -m pytest tests/

# Run with verbose output
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_models.py
```
