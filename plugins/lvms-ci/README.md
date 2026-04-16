# lvms-ci

Analyze LVMS CI periodic job failures and generate HTML release manager reports.

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install lvms-ci
```

## Skills

| Skill | Description |
|---|---|
| `/lvms-ci:doctor` | Analyze CI for multiple releases and produce an HTML summary |
| `/lvms-ci:analyze-release` | Analyze all failed LVMS periodic jobs for a single release |
| `/lvms-ci:generate-html-report` | Re-generate HTML report from existing analysis files |

## Usage

### Full pipeline
```text
/lvms-ci:doctor 4.20,4.21,4.22
```

### Single release analysis
```text
/lvms-ci:analyze-release 4.22
```

### Re-generate report
```text
/lvms-ci:generate-html-report 4.20,4.21,4.22
```

## Architecture

The pipeline follows the same pattern as `microshift-ci` and reuses shared scripts where possible:

1. **Prepare** (`doctor.sh prepare`) -- collects failed jobs and downloads artifacts
2. **Analyze** -- LLM agents analyze each job in parallel via `/ci:prow-job-analyze-test-failure`
3. **Finalize** (`doctor.sh finalize`) -- aggregates results and generates HTML

### Scripts

All scripts are shared across plugins in `plugins/shared/scripts/`:

| Script | Purpose |
|---|---|
| `doctor.sh` | Orchestrator with prepare/finalize phases (`--product lvms --filter lvm`) |
| `prow-jobs-for-release.sh` | Fetch failed periodic jobs from Prow API (`--filter lvm`) |
| `download-jobs.sh` | Download job artifacts in parallel |
| `aggregate.py` | Aggregate per-job reports into release summary JSON |
| `create-report.py` | Generate HTML report (`--product lvms` enables index image section) |

### LVMS-Specific Features

- **Index image extraction**: Per-job analysis extracts the LVMS catalog index image (digest, build date, source commit) and displays it in the HTML report
- **Prow API**: Uses the standard Prow `data.js` API to discover LVMS periodic jobs

## Requirements

- `gcloud` CLI (for downloading artifacts from public GCS buckets)
- `skopeo` (for index image inspection)
- Python 3
- **Category:** ci-cd

## Author

kasturinarra
