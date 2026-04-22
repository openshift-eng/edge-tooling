# edge-ocp-ci

Automated monitoring of OpenShift nightly payload health across edge topologies (SNO, TNA, TNF). Generates an interactive HTML dashboard with failure analysis, JIRA integration, Sippy regressions, Component Readiness comparisons, and optional timing insights. When run as a Claude Code skill, blocking job failures are automatically analyzed by AI subagents.

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install edge-ocp-ci
```

## Skills

| Skill | Description |
|---|---|
| `/edge-ocp-ci:generate-dashboard` | Collect data, generate HTML dashboard, and run AI analysis on blocking failures |

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
- `JIRA_TOKEN` environment variable — for bug matching (read-only, optional)
- Marketplace CI skills from [ai-helpers](https://github.com/openshift-eng/ai-helpers) — for deep analysis of blocking failures
- **Category:** ci-cd

## How It Works

1. Runs the `payload-monitor` Python tool to collect data from Release Controller, Sippy, Component Readiness, Prow, and JIRA
2. Generates a self-contained HTML dashboard report
3. Parses blocking job failures from the tool's stdout
4. Spawns AI subagents (one per blocking failure) to perform root cause analysis using marketplace CI skills
5. Patches AI analysis cards into the HTML report

For detailed architecture, configuration, and CLI reference, see [`payload-monitor/README.md`](../../payload-monitor/README.md).

## Author

vimauro
