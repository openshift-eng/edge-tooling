# LVMS CI Plugin

Claude Code plugin for LVMS (Logical Volume Manager Storage) CI triage and automation.

## CI Doctor Pipeline

The full CI doctor pipeline is driven by `run-doctor.py`, which runs the
`prepare → analyze → bugs → finalize` stages for the `lvm-operator` component:

```bash
python3 plugins/lvms-ci/scripts/run-doctor.py \
    --releases main --workdir /tmp/workdir
```

### Bug correlation

The `bugs` stage runs `search-bugs.py --component lvm-operator --pipeline`
(deterministic Jira search + categorization — no LLM). It searches OCPBUGS under
the "Logical Volume Manager Storage" component for existing duplicates and closed
regressions, then populates the HTML report's Bugs tab. Bugs are filed manually
via the prefilled "Create Bug in JIRA" buttons in the report (label
`lvms-ci-ai-generated`).

Requires `JIRA_USERNAME` and `JIRA_API_TOKEN` environment variables (Jira REST
API credentials); without them the `bugs` stage fails.

## Skills

| Skill | Description |
|-------|-------------|
| `lvms-ci:prow-job` | Analyze a single Prow job and produce a structured error report |

## Usage

```bash
# Analyze a single job
/lvms-ci:prow-job <prow-job-url-or-artifacts-dir>
```

## Architecture

This plugin reuses shared CI doctor scripts from `plugins/shared/scripts/` via
symlinks. The shared scripts are parameterized with `--component lvm-operator`
to filter for LVMS-specific Prow jobs.

## Prerequisites

- `gsutil` CLI for GCS access (uses anonymous access on public buckets)
- Internet access to fetch job data from Prow/GCS
- Bash shell, Python 3
- `JIRA_USERNAME` and `JIRA_API_TOKEN` environment variables (Jira REST API credentials for the `bugs` stage)
