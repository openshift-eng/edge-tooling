# microshift-ci

Analyze MicroShift CI failures, produce HTML reports, and draft JIRA bug suggestions.

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install microshift-ci
```

## CI Doctor Pipeline

The full CI doctor pipeline is driven by `run-doctor.py` — a deterministic Python
script that orchestrates all stages (prepare, graphs, analyze, bugs, finalize).
LLM agents are used only for per-job root cause analysis and Jira bug correlation.

```bash
python3 plugins/microshift-ci/scripts/run-doctor.py \
    --releases 4.19,4.20,4.21,4.22 --workdir /tmp/workdir

# Include pull request analysis
python3 plugins/microshift-ci/scripts/run-doctor.py \
    --releases 4.19,4.20,4.21,4.22 --workdir /tmp/workdir \
    --pull-requests --repo openshift/microshift

# Run specific stages only
python3 plugins/microshift-ci/scripts/run-doctor.py \
    --releases 4.22 --workdir /tmp/workdir --stages analyze,finalize
```

## Skills

| Skill | Description |
|---|---|
| `/microshift-ci:prow-job` | Root cause analysis of a single Prow job |
| `/microshift-ci:test-job` | Comprehensive job metadata and scenario results |
| `/microshift-ci:test-scenario` | Analyze individual test scenario results |
| `/microshift-ci:find-regressions` | Search JIRA for pre-existing bugs and draft ticket suggestions |
| `/microshift-ci:close-stale-bugs` | Close stale, unlinked, unassigned AI-generated bugs (dry-run by default) |
| `/microshift-ci:continue-session` | Download CI Doctor artifacts from a completed prow job |
| `/microshift-ci:fix-test-bugs` | Attempt to fix CI bugs by opening PRs in openshift/microshift (dry-run by default) |

## Usage

### Full pipeline

```bash
python3 plugins/microshift-ci/scripts/run-doctor.py \
    --releases 4.19,4.20,4.21,4.22 --workdir /tmp/workdir
```

### Single job analysis

```text
/microshift-ci:prow-job https://prow.ci.openshift.org/view/gs/test-platform-results/logs/<job-name>/<job-id>
```

### Search for bugs and draft suggestions

```text
/microshift-ci:find-regressions 4.22
```

Searches JIRA for pre-existing bugs matching CI failures and drafts ticket
suggestions for unmatched ones. Bugs are filed manually via the prefilled
"Create Bug in JIRA" buttons in the HTML report.

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
