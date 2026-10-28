# LVMS CI Plugin

Claude Code plugin for LVMS (Logical Volume Manager Storage) CI triage and automation.

## CI Doctor Pipeline

The full CI doctor pipeline is driven by `run-doctor.py`:

```bash
python3 plugins/lvms-ci/scripts/run-doctor.py \
    --releases main --workdir /tmp/workdir
```

## Skills

| Skill | Description |
|-------|-------------|
| `lvms-ci:prow-job` | Analyze a single Prow job and produce a structured error report |
| `lvms-ci:create-bugs` | Create OCPBUGS JIRA bugs from CI failure reports with cross-release dedup (dry-run by default) |

## Usage

```bash
# Analyze a single job
/lvms-ci:prow-job <prow-job-url-or-artifacts-dir>

# Correlate failures with JIRA and file/update bugs (dry-run unless --create)
/lvms-ci:create-bugs main,4.19 [--create]
```

The `bugs` stage of the CI doctor pipeline runs `lvms-ci:create-bugs` automatically,
which populates the **Bugs** tab and the per-issue **Create Bug in JIRA** button,
tracked-bug links, and regression links in the **Periodics** tab of the HTML report.
Bugs are filed in `OCPBUGS` under component "Logical Volume Manager Storage" and
labeled `lvms-ci-ai-generated`.

## Architecture

This plugin reuses shared CI doctor scripts from `plugins/shared/scripts/` via
symlinks. The shared scripts are parameterized with `--component lvm-operator`
to filter for LVMS-specific Prow jobs.

## Prerequisites

- `gsutil` CLI for GCS access (uses anonymous access on public buckets)
- Internet access to fetch job data from Prow/GCS
- Bash shell, Python 3
