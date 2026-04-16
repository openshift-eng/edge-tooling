---
name: lvms-ci:analyze-release
argument-hint: <release-version>
description: Analyze all failed LVMS periodic jobs for a single release
user-invocable: true
allowed-tools: Skill, Bash, Read, Write, Glob, Grep, Agent
---

# lvms-ci:analyze-release

## Synopsis
```bash
/lvms-ci:analyze-release <release-version>
```

## Description
Fetches failed LVMS periodic jobs for a release, downloads artifacts, analyzes each job via `/ci:prow-job-analyze-test-failure`, and produces an aggregated summary. This is a standalone version of what `/lvms-ci:doctor` does for a single release.

## Arguments
- `<release-version>` (required): e.g., 4.22, 4.21

## Scripts Directory

Shared scripts are in:
```bash
SHARED_SCRIPTS=plugins/shared/scripts
```

## Work Directory
```bash
WORKDIR=/tmp/lvms-ci-claude-workdir.$(date +%y%m%d)
```

## Steps

### Step 1: Prepare -- Collect and Download Artifacts
1. `WORKDIR=/tmp/lvms-ci-claude-workdir.$(date +%y%m%d)`
2. Run:
   ```bash
   bash ${SHARED_SCRIPTS}/doctor.sh prepare --product lvms --filter lvm --workdir ${WORKDIR} <release>
   ```
3. Read the JSON output. If no failed jobs, report success and stop.

### Step 2: Analyze Each Job
For each failed job, launch a separate **Agent** with `run_in_background: true`:

```
This is an LVMS job. Artifacts are in gs://test-platform-results/.
Some build-log.txt files are gzip-compressed -- pipe through zcat if binary.

Before analyzing test failures, check artifacts/<TEST_NAME>/lvms-catalogsource/finished.json -- if "passed":false, that is the root cause. Report it and skip test analysis.

## Extract Index Image Info
Before running test analysis, extract the LVMS catalog index image from the job artifacts:
1. Fetch artifacts/<TEST_NAME>/lvms-catalogsource/build-log.txt (may be gzip-compressed)
2. Look for the line containing "LVM_INDEX_IMAGE is set to:" and extract the image reference
3. If found, run skopeo inspect --no-tags "docker://<INDEX_IMAGE>" to get:
   - Digest, Build date, Source commit
4. Include this in the report under an "## Index Image" section

Run /ci:prow-job-analyze-test-failure <ARTIFACTS_DIR>

Save the full report to: <WORKDIR>/analyze-ci-release-<RELEASE>-job-<N>-<JOB_ID>.txt
```

Launch ALL agents in parallel. Wait for all to complete.

### Step 3: Finalize
1. Run:
   ```bash
   bash ${SHARED_SCRIPTS}/doctor.sh finalize --product lvms --workdir ${WORKDIR} <release>
   ```
2. Display the summary and path to the generated HTML report.

## Prerequisites
- `gcloud` CLI installed (for downloading artifacts from public GCS buckets)
- `skopeo` for index image inspection
- Python 3
