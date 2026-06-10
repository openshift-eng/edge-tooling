# microshift-ci

Analyze MicroShift CI failures, produce HTML reports, and create JIRA bugs.

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install microshift-ci
```

## Skills

| Skill | Description |
|---|---|
| `/microshift-ci:doctor` | Analyze CI for multiple releases and produce an HTML summary |
| `/microshift-ci:prow-job` | Root cause analysis of a single Prow job |
| `/microshift-ci:test-job` | Comprehensive job metadata and scenario results |
| `/microshift-ci:test-scenario` | Analyze individual test scenario results |
| `/microshift-ci:create-bugs` | Search JIRA for duplicates and create bugs (dry-run by default) |
| `/microshift-ci:close-stale-bugs` | Close stale, unlinked, unassigned AI-generated bugs (dry-run by default) |
| `/microshift-ci:doctor-refresh` | Regenerate the HTML report from existing data |
| `/microshift-ci:continue-session` | Download CI Doctor artifacts from a completed prow job |
| `/microshift-ci:fix-test-bugs` | Attempt to fix CI bugs by opening PRs in openshift/microshift (dry-run by default) |

## Usage

### Full pipeline

```text
/microshift-ci:doctor 4.19,4.20,4.21,4.22
```

### Single job analysis

```text
/microshift-ci:prow-job https://prow.ci.openshift.org/view/gs/test-platform-results/logs/<job-name>/<job-id>
```

### Create bugs from analysis

```text
/microshift-ci:create-bugs 4.22           # dry-run
/microshift-ci:create-bugs 4.22 --create  # create/update bugs
```

## Requirements

- `gsutil` CLI (uses anonymous access on public GCS buckets)
- `gh` CLI (authenticated with access to openshift/microshift)
- Jira MCP server configured (for bug correlation)
- Python 3
- `pcp-export-pcp2json` (for PCP performance graphs)
- `matplotlib` Python package (for PCP graph plotting)
- **Category:** ci-cd

## Author

ggiguash
