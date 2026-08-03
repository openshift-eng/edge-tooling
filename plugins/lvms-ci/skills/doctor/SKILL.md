---
name: lvms-ci:doctor
argument-hint: [release1,release2,...]
description: Analyze CI for LVMS periodic jobs and produce an HTML summary
user-invocable: true
allowed-tools: Skill, Bash, Read, Write, Glob, Grep, Agent
---

# lvms-ci:doctor

## Synopsis

```bash
/lvms-ci:doctor main
/lvms-ci:doctor 4.20,4.21,4.22,main
```

## Description

Accepts a comma-separated list of release versions (or `main`), runs analysis for each release, and produces a single HTML summary file consolidating all results. Uses deterministic scripts for data collection, artifact download, aggregation, and HTML generation. LLM agents are used only for per-job root cause analysis.

## Arguments

- `--prepared` (optional): Artifacts have already been collected by an external script. When set, skip Step 1. Read the prepare summary from `<WORKDIR>/prepare-summary.json` instead.
- `<ARGUMENTS>` (required): Comma-separated list of release versions (e.g., `main` or `4.20,4.21,4.22,main`)

## Work Directory

Compute once at the start by running `date +%y%m%d` and substituting into the path below. In all commands, replace `<WORKDIR>` with the computed path — do not use shell variables.

```text
/tmp/lvm-operator-ci-claude-workdir.<YYMMDD>
```

## Implementation Steps

### Step 1: Prepare — Collect and Download All Artifacts

**Goal**: Deterministically collect all failed jobs and download their artifacts before any LLM analysis.

1. Determine today's `<WORKDIR>` by running `date +%y%m%d` and substituting into `/tmp/lvm-operator-ci-claude-workdir.<YYMMDD>`. Use this value in all subsequent commands.

**If `--prepared` was passed**: the prepare script was already run externally. Read the prepare summary using `Read <WORKDIR>/prepare-summary.json`. Parse the JSON to get the workdir, release info (job counts, file paths), and PR info. Then skip to Step 2.

**Otherwise** run the prepare script:

1. Run:

   ```text
   bash plugins/lvms-ci/scripts/doctor.sh prepare --component lvm-operator --workdir <WORKDIR> <ARGUMENTS> --pull-requests
   ```

2. The script deterministically:
   - For each release: fetches failed periodic jobs, downloads artifacts, writes `<WORKDIR>/jobs/release-<version>-jobs.json`
   - For PRs: fetches PRs with failures, downloads artifacts, writes `<WORKDIR>/jobs/prs-jobs.json` and `<WORKDIR>/jobs/prs-status.json`
   - Outputs a JSON summary listing all releases, job counts, and file paths
3. Read the JSON output to know which releases have jobs to analyze and how many

**Job JSON field names** (use these exactly — do NOT guess alternatives like `job_name`):

- `job` — full job name
- `build_id` — unique build identifier
- `artifacts_dir` — local path to downloaded artifacts
- `url` — Prow job URL
- `status` — job result (`failure`, `FAILURE`, `SUCCESS`, `PENDING`)
- `pr_number` — PR number (PR jobs only)

**Error Handling**:

- If `<ARGUMENTS>` is empty, show usage and stop
- If a release has no failed jobs, its jobs JSON will be an empty array — skip analysis for that release
- If a release has an `"error"` field in the JSON summary, data collection failed for that release — report the error to the user but continue with other releases

### Step 2: Analyze Each Job Using prow-job-analyzer Agent

**Goal**: Get detailed root cause analysis for each failed job using pre-downloaded artifacts.

**Actions**:

1. Use the JSON summary output from Step 1 to build agent prompts. Do NOT read the job JSON files into the main conversation — the prepare script already printed all job details (artifacts_dir, build_id, job name, url) and agents receive these directly in their prompt.
2. For **every** failed job across all releases and PRs, launch a separate **Agent** (using the `Agent` tool, NOT the `Skill` tool) with `subagent_type=lvms-ci:prow-job-analyzer`. For PR jobs, only launch agents for jobs with FAILURE status.

   The agent returns a JSON array directly — no extraction needed. Build the prompt with the job's `artifacts_dir`, `url` (as `job_url`), and `job` (as `job_name`) from the prepare output.

   **Example prompt:**

   ```text
   Analyze this prow job:
   artifacts_dir: <ARTIFACTS_DIR>
   job_url: <JOB_URL>
   job_name: <JOB_NAME>
   ```

   After each agent completes, save its JSON response to the corresponding file using the Write tool:
   - Release jobs: `<WORKDIR>/jobs/release-<RELEASE>-job-<N>-<JOB_ID>.json`
   - PR jobs: `<WORKDIR>/jobs/prs-job-<N>-pr<PR_NUMBER>-<JOB_NAME_SUFFIX>.json`

3. Launch **ALL** agents (all releases + PRs) in a **single message** as **foreground** agents (do NOT use `run_in_background`). Foreground agents in the same message run concurrently — this is just as fast as background agents but keeps your turn active until all complete.
4. Say "Analyzing N jobs in parallel..." in your message text alongside the Agent tool calls.
5. When all agents return, immediately proceed to Step 3 in the same turn. Do NOT stop or end your turn between Step 2 and Step 3.

### Step 3: Finalize — Aggregate and Generate HTML Report

**IMPORTANT**: This step is MANDATORY. The task is incomplete without it. You MUST run this even if previous steps produced errors.

**Goal**: Deterministically aggregate results and generate the HTML report.

**Actions**:

1. Run the finalize script:

   ```text
   bash plugins/lvms-ci/scripts/doctor.sh finalize --component lvm-operator --workdir <WORKDIR> <ARGUMENTS>
   ```

2. The script deterministically:
   - Runs `aggregate.py` for each release and for PRs → `summary.json` files
   - Runs `create-report.py` → `report-lvm-operator-ci-doctor.html`
3. Report the script's output to the user

### Step 4: Report Completion

**Actions**:

1. Display the path to the generated HTML file
2. Summarize: failed job counts per release, PR status

**Example Output**:

```text
Summary:
  Periodics:
    Release main: 3 failed periodic jobs
    Release 4.22: 0 failed periodic jobs
  Pull Requests:
    4 PRs with 6 total failed jobs

HTML report generated: <WORKDIR>/report-lvm-operator-ci-doctor.html
```

## Examples

### Example 1: Analyze Main Branch Only

```bash
/lvms-ci:doctor main
```

### Example 2: Analyze Multiple Releases

```bash
/lvms-ci:doctor 4.20,4.21,4.22,main
```

## Prerequisites

- `gsutil` CLI must be installed for GCS access (uses anonymous access on public buckets)
- Internet access to fetch job data from Prow/GCS
- Bash shell, Python 3

### Z-Stream PR Results

Z-stream test results are collected automatically when `--pull-requests` is passed to the prepare command. The `--pull-requests` flag queries open PRs targeting the `release-management` branch on `openshift/lvm-operator` and fetches their Prow presubmit results from GCS. These results appear in the **Pull Requests** tab of the HTML report.

## Related Skills

- **lvms-ci:prow-job**: Single job analysis (thin wrapper around the `lvms-ci:prow-job-analyzer` agent)
- **lvms-ci:prow-job-analyzer**: Dedicated agent for root cause analysis of a single prow job (used directly by Step 2)

## Notes

- **Deterministic scripts** handle: data collection, artifact download, aggregation, HTML generation
- **LLM agents** handle: per-job root cause analysis (Step 2)
- All agents are launched in a single parallel wave
- The `prepare` script downloads all artifacts upfront so prow-job agents use local paths (no redundant downloads)
- The `finalize` script runs aggregation and HTML generation in one call
- All intermediate files use prescribed filenames in `<WORKDIR>` — no improvised names
- The HTML report is self-contained (no external CSS/JS dependencies)
- If a release analysis fails, it is noted in the report but does not block other releases
