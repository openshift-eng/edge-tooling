# edge-ocp-ci

Automated monitoring of OpenShift nightly payload health across edge topologies (SNO, TNA, TNF). Generates an interactive HTML dashboard with failure analysis, suggested JIRA bug links, Sippy regressions, Component Readiness comparisons, and optional timing insights. When run as a Claude Code skill, blocking job failures and the worst five Sippy job-health findings are automatically analyzed by AI, reusing evidence when they overlap.

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install edge-ocp-ci
```

## Skills

| Skill | Description |
|---|---|
| `/edge-ocp-ci:generate-dashboard` | Collect data, generate HTML dashboard, and run AI analysis on blocking failures and the worst five Sippy jobs |

## Usage

```text
# Run with defaults (all configured versions)
/edge-ocp-ci:generate-dashboard

# Override versions
/edge-ocp-ci:generate-dashboard --versions 4.19,4.20

# Skip slow collectors
/edge-ocp-ci:generate-dashboard --skip-prow --skip-sippy

# Include timing insights
/edge-ocp-ci:generate-dashboard --with-timing
```

## Requirements

- Python 3 (venv created automatically)
- `gsutil` (Google Cloud SDK) — for Prow artifact fetching
- Marketplace CI skills from [ai-helpers](https://github.com/openshift-eng/ai-helpers) — for deep analysis of blocking failures and selected Sippy jobs
- **Category:** ci-cd

## How It Works

1. Runs the `payload-monitor` Python tool to collect data from Release Controller, Sippy, Component Readiness, and Prow
2. Generates a self-contained HTML dashboard report
3. Reads blocking job failures from stdout and the exact selected Sippy jobs from the report's `.ai-targets.json` file
4. Analyzes blocking failures and every selected Sippy job using marketplace CI skills, reusing investigations of identical Prow runs
5. Patches AI analysis cards into blocking details and the matching expandable Sippy Job Health Status rows, including when no blocking jobs failed

For detailed architecture, configuration, and CLI reference, see [`payload-monitor/README.md`](../../payload-monitor/README.md). For a Sippy Component Readiness-only triage report (no payload/Prow collection), see [`ci-tooling/readiness-report/README.md`](../../ci-tooling/readiness-report/README.md).

## Author

vimauro
