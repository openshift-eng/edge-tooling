---
name: lvms-ci:doctor
argument-hint: [release1,release2,...]
description: Analyze CI for LVMS periodic jobs and produce an HTML summary
user-invocable: true
allowed-tools: Bash, Read, Write, Glob, Grep, Workflow
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

- `<ARGUMENTS>` (required): Comma-separated list of release versions (e.g., `main` or `4.20,4.21,4.22,main`)

## Work Directory

Compute once at the start by running `date +%y%m%d` and substituting into the path below. In all commands, replace `<WORKDIR>` with the computed path — do not use shell variables.

```text
/tmp/lvm-operator-ci-claude-workdir.<YYMMDD>
```

## Implementation Steps

### Step 1: Prepare — Collect and Download All Artifacts

**Goal**: Deterministically collect all failed jobs and download their artifacts before any LLM analysis.

**Actions**:

1. Determine today's `<WORKDIR>` by running `date +%y%m%d` and substituting into `/tmp/lvm-operator-ci-claude-workdir.<YYMMDD>`. Use this value in all subsequent commands.
2. Run the prepare script:

   ```text
   bash plugins/lvms-ci/scripts/doctor.sh prepare --component lvm-operator --workdir <WORKDIR> <ARGUMENTS> --pull-requests
   ```

3. The script deterministically:
   - For each release: fetches failed periodic jobs, downloads artifacts, writes `<WORKDIR>/jobs/release-<version>-jobs.json`
   - For PRs: fetches PRs with failures, downloads artifacts, writes `<WORKDIR>/jobs/prs-jobs.json` and `<WORKDIR>/jobs/prs-status.json`
   - Outputs a JSON summary listing all releases, job counts, and file paths
4. Read the JSON output to know which releases have jobs to analyze and how many

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

### Step 2: Analyze Each Job Using Workflow

**Goal**: Get detailed root cause analysis for each failed job using pre-downloaded artifacts. Uses the Workflow tool to guarantee parallel execution.

**Actions**:

1. Use the JSON summary output from Step 1 to build a `jobs` array. Do NOT read the job JSON files into the main conversation — the prepare script already printed all job details (artifacts_dir, build_id, job name).
2. For **every** failed job across all releases and PRs (for PR jobs, only those with FAILURE status), create a job object with `prompt` and `label` fields.

   **Prompt template for release jobs:**

   ```text
   Analyze this prow job:
   artifacts_dir: <ARTIFACTS_DIR>
   job_url: <JOB_URL>
   job_name: <JOB_NAME>
   ```

   **Prompt template for PR jobs:**

   ```text
   Analyze this prow job:
   artifacts_dir: <ARTIFACTS_DIR>
   job_url: <JOB_URL>
   job_name: <JOB_NAME>
   ```

   Substitute `<ARTIFACTS_DIR>`, `<JOB_URL>`, and `<JOB_NAME>` in the prompt templates from the prepare script's JSON output (`artifacts_dir`, `url`, `job` fields). The remaining variables `<JOB_ID>`, `<RELEASE>`, and `<PR_NUMBER>` (from `build_id`, `release`, `pr_number` fields) are used in labels and filenames below.

   **Label**: Use a short identifier like `<RELEASE>/<JOB_NAME_SUFFIX>` for release jobs or `pr<PR_NUMBER>/<JOB_NAME_SUFFIX>` for PR jobs.

3. Call the **Workflow** tool with ALL jobs passed directly as args:

   ```text
   Workflow: scriptPath="plugins/lvms-ci/scripts/agent-workflow.js", args={agentType: "lvms-ci:prow-job-analyzer", jobs: [<jobs array>]}
   ```

4. The Workflow runs in the background. Immediately call `TaskOutput(task_id=<ID>, block=true, timeout=600000)` to wait for it. If it returns with status `running`, call `TaskOutput` again — repeat until the workflow completes. Do NOT end your turn while the workflow is still running.
5. When the workflow completes, it returns `{ analyzed, failed, total, results }` where `results[i]` is the agent's JSON response for `jobs[i]` (null for failed agents). Save each non-null result to the corresponding file using the Write tool:
   - Release jobs: `<WORKDIR>/jobs/release-<RELEASE>-job-<N>-<JOB_ID>.json`
   - PR jobs: `<WORKDIR>/jobs/prs-job-<N>-pr<PR_NUMBER>-<JOB_NAME_SUFFIX>.json`
6. Report the analysis counts and immediately proceed to Step 3. Do NOT stop or end your turn between Step 2 and Step 3.

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

- **lvms-ci:prow-job**: Single job analysis (used by Step 2 workflow agents, also standalone)

## Notes

- **Deterministic scripts** handle: data collection, artifact download, aggregation, HTML generation
- **LLM agents** handle: per-job root cause analysis (Step 2)
- Step 2 uses the Workflow tool to guarantee parallel agent execution — all agents run concurrently
- The `prepare` script downloads all artifacts upfront so prow-job agents use local paths (no redundant downloads)
- The `finalize` script runs aggregation and HTML generation in one call
- All intermediate files use prescribed filenames in `<WORKDIR>` — no improvised names
- The HTML report is self-contained (no external CSS/JS dependencies)
- If a release analysis fails, it is noted in the report but does not block other releases
