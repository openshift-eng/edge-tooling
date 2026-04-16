---
name: lvms-ci:doctor
argument-hint: <release1,release2,...>
description: Analyze CI for multiple LVMS releases and produce an HTML summary
user-invocable: true
allowed-tools: Skill, Bash, Read, Write, Glob, Grep, Agent
---

# lvms-ci:doctor

## Synopsis
```bash
/lvms-ci:doctor <release1,release2,...>
```

## Description
Accepts a comma-separated list of release versions, runs analysis for each release, and produces a single HTML summary file consolidating all results. Uses deterministic scripts for data collection, artifact download, aggregation, and HTML generation. LLM agents are used only for per-job root cause analysis.

## Arguments
- `$ARGUMENTS` (required): Comma-separated list of release versions (e.g., `4.20,4.21,4.22`)

## Scripts Directory

Shared scripts are in:
```bash
SHARED_SCRIPTS=plugins/shared/scripts
```

## Work Directory

Set once at the start and reference throughout:
```bash
WORKDIR=/tmp/lvms-ci-claude-workdir.$(date +%y%m%d)
```

## Implementation Steps

### Step 1: Prepare -- Collect and Download All Artifacts

**Goal**: Deterministically collect all failed jobs and download their artifacts before any LLM analysis.

**Actions**:
1. Run `WORKDIR=/tmp/lvms-ci-claude-workdir.$(date +%y%m%d)` using the `Bash` tool
2. Run the prepare script:
   ```bash
   bash ${SHARED_SCRIPTS}/doctor.sh prepare --product lvms --filter lvm --workdir ${WORKDIR} $ARGUMENTS
   ```
3. The script deterministically:
   - For each release: fetches failed periodic jobs, downloads artifacts, writes `${WORKDIR}/analyze-ci-release-<version>-jobs.json`
   - Outputs a JSON summary listing all releases, job counts, and file paths
4. Read the JSON output to know which releases have jobs to analyze and how many

**Error Handling**:
- If `$ARGUMENTS` is empty, show usage and stop
- If a release has no failed jobs, its jobs JSON will be an empty array -- skip analysis for that release

### Step 2: Analyze Each Job Using /lvms-ci:analyze-release

**Goal**: Get detailed root cause analysis for each failed job using pre-downloaded artifacts.

**Actions**:
1. Use the JSON summary output from Step 1 to build agent prompts. Do NOT read the job JSON files into the main conversation -- the prepare script already printed all job details (artifacts_dir, build_id, job name) and agents receive artifacts_dir directly in their prompt.
2. For **every** failed job across all releases, launch a separate **Agent** (using the `Agent` tool, NOT the `Skill` tool).

   ```text
   Agent: subagent_type=general_purpose, prompt="Analyze this LVMS Prow job and save the report:

   This is an LVMS job. Artifacts are in gs://test-platform-results/.
   Some build-log.txt files are gzip-compressed -- pipe through zcat if binary.

   Before analyzing test failures, check artifacts/<TEST_NAME>/lvms-catalogsource/finished.json -- if 'passed':false, that is the root cause. Report it and skip test analysis.

   ## Extract Index Image Info
   Before running test analysis, extract the LVMS catalog index image from the job artifacts:
   1. Fetch artifacts/<TEST_NAME>/lvms-catalogsource/build-log.txt (may be gzip-compressed)
   2. Look for the line containing 'LVM_INDEX_IMAGE is set to:' and extract the image reference
   3. If found, run skopeo inspect --no-tags 'docker://<INDEX_IMAGE>' to get:
      - Digest (sha256)
      - Build date (from org.opencontainers.image.created label)
      - Source commit (from vcs-ref or org.opencontainers.image.revision label)
   4. Include this in the report under an '## Index Image' section

   Run /ci:prow-job-analyze-test-failure <ARTIFACTS_DIR>

   Save the full report to: ${WORKDIR}/analyze-ci-release-<RELEASE>-job-<N>-<BUILD_ID>.txt"
   ```

3. Launch **ALL** agents in a single message using `run_in_background: true`
4. After launching, say "Analyzing N jobs in parallel..." and STOP.
5. As agent completion notifications arrive, respond with only "." (a single period).
6. Only after ALL agents are confirmed complete, proceed to Step 3.

### Step 3: Finalize -- Aggregate and Generate HTML Report

**Goal**: Deterministically aggregate results and generate the HTML report.

**Actions**:
1. Run the finalize script:
   ```bash
   bash ${SHARED_SCRIPTS}/doctor.sh finalize --product lvms --workdir ${WORKDIR} $ARGUMENTS
   ```
2. The script deterministically:
   - Runs `aggregate.py` for each release -> `summary.json` files
   - Runs `create-report.py` -> `lvms-ci-doctor-report.html`
3. Report the script's output to the user

### Step 4: Report Completion

**Actions**:
1. Display the path to the generated HTML file
2. Summarize: failed job counts per release

**Example Output**:
```text
Summary:
  Release 4.20: 3 failed periodic jobs
  Release 4.21: 0 failed periodic jobs
  Release 4.22: 7 failed periodic jobs

HTML report generated: ${WORKDIR}/lvms-ci-doctor-report.html
```

## Examples

### Example 1: Analyze Multiple Releases
```bash
/lvms-ci:doctor 4.20,4.21,4.22
```

### Example 2: Single Release
```bash
/lvms-ci:doctor 4.22
```

## Prerequisites

- `gcloud` CLI installed (for downloading artifacts from public GCS buckets)
- `skopeo` for index image inspection
- Python 3
- Bash shell

## Notes
- **Deterministic scripts** handle: data collection, artifact download, aggregation, HTML generation
- **LLM agents** handle: per-job root cause analysis (Step 2)
- All agents are launched in a single parallel wave
- The `prepare` script downloads all artifacts upfront so prow-job agents use local paths
- The `finalize` script runs aggregation and HTML generation in one call
- All intermediate files use prescribed filenames in `${WORKDIR}`
- The HTML report is self-contained (no external CSS/JS dependencies)
