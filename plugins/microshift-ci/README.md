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
LLM agents are used only for per-job root cause analysis.

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

Searches JIRA for pre-existing bugs matching CI failures and drafts ticket
suggestions for unmatched ones. The suggest/linked/skip decisions are written
to `<WORKDIR>/bugs/bug-search-<release>.txt` when one release is analyzed, or
to `<WORKDIR>/report-bug-search.txt` when several releases are analyzed. Bugs
are filed manually via the prefilled "Create Bug in JIRA" buttons in the HTML
report. The buttons follow the categorization decisions: they appear only for
failures categorized as `suggest`; `linked` and `skipped` failures show their
decision instead. A closed bug is listed only when its summary or description
names the failure's distinctive terms (version numbers do not count); it is
shown as possibly related, with the matched terms, and the failure stays
`suggest` because Jira cannot show whether the fix was in the tested build.

## Requirements

- `gsutil` CLI (uses anonymous access on public GCS buckets)
- `gh` CLI (authenticated with access to openshift/microshift)
- `JIRA_USERNAME` and `JIRA_API_TOKEN` environment variables (Jira REST API credentials for bug correlation)
- Jira MCP server configured (only for `/microshift-ci:close-stale-bugs` `--close` mode)
- Python 3
- `pcp-export-pcp2json` (for PCP performance graphs)
- `matplotlib` Python package (for PCP graph plotting)
- **Category:** ci-cd

## Author

ggiguash
