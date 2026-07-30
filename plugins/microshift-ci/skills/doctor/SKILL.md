---
name: microshift-ci:doctor
argument-hint: <release1,release2,...>
description: Analyze CI for multiple MicroShift releases and produce an HTML summary
user-invocable: true
allowed-tools: Bash, Read, Write, Glob, Grep, Agent, Workflow
---

# microshift-ci:doctor

## Synopsis

```bash
/microshift-ci:doctor <release1,release2,...>
```

## Description

Accepts a comma-separated list of MicroShift release versions, runs analysis for each release and for open rebase PRs, and produces a single HTML summary file consolidating all results. Uses deterministic scripts for data collection, artifact download, aggregation, and HTML generation. LLM agents are used only for per-job root cause analysis and Jira bug correlation.

## Arguments

- `<ARGUMENTS>` (required): Comma-separated list of release versions (e.g., `4.19,4.20,4.21,4.22`)

## Work Directory

Compute once at the start by running `date +%y%m%d` and substituting into the path below. In all commands, replace `<WORKDIR>` with the computed path — do not use shell variables.

```text
/tmp/microshift-ci-claude-workdir.<YYMMDD>
```

## Implementation Steps

### Step 1: Prepare — Collect and Download All Artifacts

**Goal**: Deterministically collect all failed jobs and download their artifacts before any LLM analysis.

**Actions**:

1. Determine today's `<WORKDIR>` by running `date +%y%m%d` and substituting into `/tmp/microshift-ci-claude-workdir.<YYMMDD>`. Use this value in all subsequent commands.
2. Run the prepare script:

   ```text
   bash plugins/microshift-ci/scripts/doctor.sh prepare --component microshift --workdir <WORKDIR> <ARGUMENTS> --pull-requests --repo openshift/microshift
   ```

3. The script deterministically:
   - For each release: fetches failed periodic jobs, downloads artifacts, writes `<WORKDIR>/jobs/release-<version>-jobs.json`
   - For rebase PRs: fetches PRs with failures, downloads artifacts, writes `<WORKDIR>/jobs/prs-jobs.json` and `<WORKDIR>/jobs/prs-status.json`
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

### Step 1b: Generate PCP Performance Graphs

**Goal**: Generate performance graphs from PCP archives for all jobs that have pmlogs.

**Actions**:

1. Run the graphs script (this is deterministic, no LLM needed):

   ```text
   bash plugins/microshift-ci/scripts/doctor.sh graphs --component microshift --workdir <WORKDIR>
   ```

2. The script finds PCP archives in downloaded artifacts and generates PNG graphs at `<WORKDIR>/graphs/<build_id>/`:
   - `1_cpu_usage.png` — CPU usage (user, system, I/O wait)
   - `2_mem_usage.png` — Memory usage (used, cached)
   - `3_disk_io.png` — Disk I/O (read/write OPS, await)
   - `4_disk_usage.png` — Disk usage by partition (% fill)
3. If prerequisites are missing (`pcp2json`, `matplotlib`), the script errors and stops.

### Step 2: Analyze Each Job Using Workflow

**Goal**: Get detailed root cause analysis for each failed job using pre-downloaded artifacts. Uses the Workflow tool to guarantee parallel execution.

**Actions**:

1. Use the JSON summary output from Step 1 to build a `jobs` array. Do NOT read the job JSON files into the main conversation — the prepare script already printed all job details (artifacts_dir, build_id, job name).
2. For **every** failed job across all releases and PRs (for PR jobs, only those with FAILURE status), create a job object with `prompt` and `label` fields.

   **Prompt template for release jobs:**

   ```text
   Analyze this prow job:
   artifacts_dir: <ARTIFACTS_DIR>
   graphs_dir: <WORKDIR>/graphs/<JOB_ID>
   source_dir: <WORKDIR>/src/microshift-release-<RELEASE> (or <WORKDIR>/src/microshift for main)
   job_url: <JOB_URL>
   job_name: <JOB_NAME>
   ```

   **Prompt template for PR jobs:**

   ```text
   Analyze this prow job:
   artifacts_dir: <ARTIFACTS_DIR>
   graphs_dir: <WORKDIR>/graphs/<BUILD_ID>
   source_dir: <WORKDIR>/src/microshift
   job_url: <JOB_URL>
   job_name: <JOB_NAME>
   ```

   Substitute `<ARTIFACTS_DIR>`, `<JOB_ID>`/`<BUILD_ID>`, `<RELEASE>`, `<JOB_URL>`, and `<JOB_NAME>` from the prepare script's JSON output (`artifacts_dir`, `build_id`, `release`, `url`, `job` fields). Only include `graphs_dir` and `source_dir` if those directories exist.

   **Label**: Use a short identifier like `<RELEASE>/<JOB_NAME_SUFFIX>` (e.g., `4.22/e2e-aws-tests-bootc-nightly`).

3. Call the **Workflow** tool with ALL jobs passed directly as args:

   ```text
   Workflow: scriptPath="plugins/microshift-ci/scripts/agent-workflow.js", args={agentType: "microshift-ci:prow-job-analyzer", jobs: [<jobs array>]}
   ```

4. The Workflow runs in the background. Immediately call `TaskOutput(task_id=<ID>, block=true, timeout=600000)` to wait for it. If it returns with status `running`, call `TaskOutput` again — repeat until the workflow completes. Do NOT end your turn while the workflow is still running.
5. When the workflow completes, it returns `{ analyzed, failed, total, results }` where `results[i]` is the agent's JSON response for `jobs[i]` (null for failed agents). Save each non-null result to the corresponding file using the Write tool:
   - Release jobs: `<WORKDIR>/jobs/release-<RELEASE>-job-<N>-<JOB_ID>.json`
   - PR jobs: `<WORKDIR>/jobs/prs-job-<N>-pr<PR>-<JOB_NAME_SUFFIX>.json`
6. Report the analysis counts and immediately proceed to Step 3. Do NOT stop or end your turn between Step 2 and Step 3.

### Step 3: Run Bug Correlation (Dry-Run)

**Goal**: Search Jira for existing bugs matching each failure. Results are embedded in the HTML report.

**Actions**:

1. Collect all release versions from `<ARGUMENTS>` into a comma-separated list (e.g., `4.19,4.20,4.21,4.22`)
2. Check for rebase PR source identifiers from the PR jobs JSON (e.g., `rebase-release-4.22`). Append them to the source list.
3. Launch a **single** `microshift-ci:create-bugs` **foreground** agent in dry-run mode with all sources:

   ```text
   Agent: subagent_type=general_purpose, prompt="Run /microshift-ci:create-bugs <all-sources-comma-separated>"
   ```

4. The agent produces:
   - `<WORKDIR>/bugs/bug-matches-<source>.json` for each source (mapping files with open bugs data for the Bugs tab)
   - `<WORKDIR>/report-create-bugs.txt` — merged report covering all releases and rebase sources
5. When the agent returns, immediately proceed to Step 4 in the same turn. Do NOT stop or end your turn between Step 3 and Step 4.

**Error Handling**:

- If create-bugs fails, note the failure but do not block HTML generation

### Step 4: Finalize — Aggregate and Generate HTML Report

**IMPORTANT**: This step is MANDATORY. The task is incomplete without it. You MUST run this even if previous steps produced errors.

**Goal**: Deterministically aggregate results and generate the HTML report.

**Actions**:

1. Run the finalize script:

   ```text
   bash plugins/microshift-ci/scripts/doctor.sh finalize --component microshift --workdir <WORKDIR> <ARGUMENTS>
   ```

2. The script deterministically:
   - Runs `aggregate.py` for each release and for PRs → `summary.json` files
   - Runs `create-report.py` → `report-microshift-ci-doctor.html`
3. Report the script's output to the user

### Step 5: Report Completion

**Actions**:

1. Display the path to the generated HTML file
2. Summarize: failed job counts per release, rebase PR status, bug correlation results

**Example Output**:

```text
Summary:
  Periodics:
    Release 4.19: 3 failed periodic jobs
    Release 4.20: ERROR - data collection failed
    Release 4.21: 0 failed periodic jobs
    Release 4.22: 12 failed periodic jobs
  Pull Requests:
    2 rebase PRs with 5 total failed jobs

HTML report generated: <WORKDIR>/report-microshift-ci-doctor.html
```

## Examples

### Example 1: Analyze Multiple Releases

```bash
/microshift-ci:doctor 4.19,4.20,4.21,4.22
```

### Example 2: Analyze Two Releases

```bash
/microshift-ci:doctor 4.21,4.22
```

### Example 3: Single Release (still produces HTML)

```bash
/microshift-ci:doctor 4.22
```

## Prerequisites

- `gsutil` CLI must be installed for GCS access (uses anonymous access on public buckets)
- `gh` CLI must be authenticated with access to openshift/microshift
- MCP Jira server must be configured (for bug correlation)
- Internet access to fetch job data from Prow/GCS
- Bash shell, Python 3
- `pcp-export-pcp2json` — for PCP graph generation
- `matplotlib` Python package — for PCP graph plotting

## Related Skills

- **microshift-ci:prow-job-analyzer** agent: Root cause analysis for a single job (used by Step 2 agents)
- **microshift-ci:create-bugs**: Bug correlation and creation (used in Step 3; can also be run with `--create` after this command)
- **microshift-ci:doctor-refresh**: Regenerate the HTML report from existing data (e.g., after `/microshift-ci:create-bugs --create`)

## Notes

- **Deterministic scripts** handle: data collection, artifact download, aggregation, HTML generation
- **LLM agents** handle: per-job root cause analysis (Step 2), Jira bug search and open bugs query (Step 3)
- `/microshift-ci:doctor-refresh` regenerates the HTML report from existing data. Use it after `/microshift-ci:create-bugs --create` to include newly created bugs
- Step 2 uses the Workflow tool to guarantee parallel agent execution — all prow-job-analyzer agents run concurrently
- Step 3 uses a single create-bugs agent with all sources (releases + rebase) comma-separated
- The `prepare` script downloads all artifacts upfront so prow-job agents use local paths (no redundant downloads)
- The `prepare` script also clones the MicroShift source to `<WORKDIR>/src/microshift` with per-release worktrees (`--repo openshift/microshift`); clone failure is non-fatal — agents record the absence in `analysis_gaps` and proceed
- The `finalize` script runs aggregation and HTML generation in one call
- All intermediate files use prescribed filenames in `<WORKDIR>` subdirectories (`jobs/`, `bugs/`) — no improvised names
- The HTML report is self-contained (no external CSS/JS dependencies)
- If a release analysis fails, it is noted in the report but does not block other releases
- If no rebase PRs are open, the Pull Requests tab shows "No open rebase pull requests found"
