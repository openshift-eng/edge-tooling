# LVMS CI Plugin

Claude Code plugin for LVMS (Logical Volume Manager Storage) CI triage and automation.

## Skills

| Skill | Description |
|-------|-------------|
| `lvms-ci:doctor` | Analyze CI for LVMS periodic jobs and produce an HTML summary |
| `lvms-ci:prow-job` | Analyze a single Prow job and produce a structured error report |

## Usage

```bash
# Analyze all LVMS periodic jobs
/lvms-ci:doctor main

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
