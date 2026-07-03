---
name: microshift-ci:prow-job
argument-hint: <prow-job-url-or-artifacts-dir>
description: Download Prow job artifacts, extract evidence, and analyze the failure
user-invocable: true
allowed-tools: Bash, Read, Write, Glob, Grep, Agent
---

# microshift-ci:prow-job

Analyzes a single Prow CI job. Accepts a Prow URL or local artifacts directory.
Downloads artifacts if needed, extracts structured evidence, then spawns an
analyze-evidence agent for root cause analysis.

## Arguments

`<ARGUMENTS>`: Prow URL, GCS web URL, or local artifacts directory.

URL formats — periodic: `.../logs/{JOB_NAME}/{JOB_ID}`, presubmit: `.../pr-logs/pull/openshift_microshift/{PR}/{JOB_NAME}/{JOB_ID}`.
Hosts: `prow.ci.openshift.org/view/gs/test-platform-results/...` or `gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/...`.

## Work Directory

`/tmp/microshift-ci-claude-workdir.<YYMMDD>` — compute `<YYMMDD>` once via `date +%y%m%d`.

## Workflow

The user argument is: `<ARGUMENTS>`

1. **Set up artifacts**:
   - Local path (starts with `/`): use it as `<TMP>`. Skip step 2.
   - URL: create `<TMP>` with `mktemp -d <WORKDIR>/openshift-ci-analysis-XXXX`.

2. **Download** (URL only):

   ```bash
   GCS_PATH=$(echo "<URL>" | sed -e 's|https://prow.ci.openshift.org/view/gs/|gs://|' \
                                  -e 's|https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/|gs://|')
   gsutil -q -m cp -r "${GCS_PATH}/" <TMP>/
   ```

3. **Extract evidence**:

   ```bash
   python3 plugins/shared/scripts/extract-evidence.py --artifacts-dir <TMP> --workdir <WORKDIR>
   ```

   Produces `<WORKDIR>/evidence/evidence-<BUILD_ID>.json`. The `<BUILD_ID>` is the last path component of `<TMP>`.

4. **Analyze**: Read `plugins/microshift-ci/agents/analyze-evidence.md`. The template is group-oriented; render it for a single-job group by substituting:

   | Placeholder | Value |
   |---|---|
   | `{GROUP_JOBS}` | `- <JOB_NAME> — <JOB_URL>`, then indented lines `evidence pack: <WORKDIR>/evidence/evidence-<BUILD_ID>.json` and `artifacts: <TMP>` |
   | `{OUTPUT_FILE}` | `<WORKDIR>/report-<BUILD_ID>.txt` |

   Spawn the agent with the substituted content. When it replies `DONE`, read the output file and present the report to the user.

## Prerequisites

- `gsutil` CLI (for URL input), Python 3, Bash
