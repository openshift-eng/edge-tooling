# ee-payload-monitor

Automated monitoring of OpenShift nightly payload health across edge topologies (SNO, TNA, TNF). Generates an interactive HTML dashboard with failure analysis, JIRA integration, Sippy regressions, Component Readiness comparisons, and optional timing insights. When run as a Claude Code skill, blocking job failures are automatically analyzed by AI subagents.

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install ee-payload-monitor
```

## Skills

| Skill | Description |
|---|---|
| `/ee-payload-monitor:generate-dashboard` | Collect data, generate HTML dashboard, and run AI analysis on blocking failures |

## Usage

```text
# Run with defaults (all configured versions)
/ee-payload-monitor:generate-dashboard

# Override versions
/ee-payload-monitor:generate-dashboard --versions 4.19,4.20

# Skip slow collectors
/ee-payload-monitor:generate-dashboard --skip-prow --skip-sippy

# Include timing insights
/ee-payload-monitor:generate-dashboard --with-timing
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
