---
name: microshift-ci:doctor
argument-hint: <release1,release2,...>
description: Analyze CI for multiple MicroShift releases and produce an HTML summary
user-invocable: true
allowed-tools: Skill, Bash, Read, Write, Glob, Grep, Agent
---

# microshift-ci:doctor

## Synopsis

```bash
/microshift-ci:doctor <release1,release2,...>
```

## Description

Accepts a comma-separated list of MicroShift release versions, runs analysis for each release and for open rebase PRs, and produces a single HTML summary file consolidating all results. Uses deterministic scripts for data collection, artifact download, aggregation, and HTML generation. LLM agents are used only for per-job root cause analysis and Jira bug correlation.

## Arguments

- `--prepared` (optional): Artifacts and graphs have already been collected by an external script. When set, skip Steps 1 and 1b. Read the prepare summary from `<WORKDIR>/prepare-summary.json` instead.
- `<ARGUMENTS>` (required): Comma-separated list of release versions (e.g., `4.19,4.20,4.21,4.22`)

## Work Directory

Compute once at the start by running `date +%y%m%d` and substituting into the path below. In all commands, replace `<WORKDIR>` with the computed path — do not use shell variables.

```text
/tmp/microshift-ci-claude-workdir.<YYMMDD>
```

## Implementation Steps

### Step 1: Prepare — Collect and Download All Artifacts

**Goal**: Deterministically collect all failed jobs and download their artifacts before any LLM analysis.

1. Determine today's `<WORKDIR>` by running `date +%y%m%d` and substituting into `/tmp/microshift-ci-claude-workdir.<YYMMDD>`. Use this value in all subsequent commands.

**If `--prepared` was passed**: the prepare script was already run externally. Read the prepare summary using `Read <WORKDIR>/prepare-summary.json`. Parse the JSON to get the workdir, release info (job counts, file paths), PR info, and source checkout paths. Then skip to Step 2.

**Otherwise** run the prepare script:

1. Run:

   ```text
   bash plugins/microshift-ci/scripts/doctor.sh prepare --component microshift --workdir <WORKDIR> <ARGUMENTS> --pull-requests --repo openshift/microshift
   ```

2. The script deterministically:
   - For each release: fetches failed periodic jobs, downloads artifacts, writes `<WORKDIR>/jobs/release-<version>-jobs.json`
   - For rebase PRs: fetches PRs with failures, downloads artifacts, writes `<WORKDIR>/jobs/prs-jobs.json` and `<WORKDIR>/jobs/prs-status.json`
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

### Step 1b: Generate PCP Performance Graphs

**If `--prepared` was passed**: skip this step entirely (graphs were already generated externally). Proceed to Step 2.

**Otherwise**:

**Goal**: Generate performance graphs from PCP archives for all jobs that have pmlogs.

**Actions**:

1. Run the graphs script (this is deterministic, no LLM needed):

   ```text
   bash plugins/microshift-ci/scripts/doctor.sh graphs --component microshift --workdir <WORKDIR>
   ```

2. The script finds PCP archives in downloaded artifacts and generates JSON metric files at `<WORKDIR>/graphs/<build_id>/`:
   - `cpu.json` — CPU usage (user, system, I/O wait, idle)
   - `mem.json` — Memory usage (used, cached, free, total)
   - `io.json` — Disk I/O (read/write ops, await, queue depth)
   - `disk.json` — Disk usage by partition (% fill, GB used)
3. If prerequisites are missing (`pcp2json`), the script errors and stops.

### Step 2: Analyze Each Job Using microshift-ci:prow-job-analyzer Agent

**Goal**: Get detailed root cause analysis for each failed job using pre-downloaded artifacts.

**Actions**:

1. Use the JSON summary output from Step 1 to build agent prompts. Do NOT read the job JSON files into the main conversation — the prepare script already printed all job details (artifacts_dir, build_id, job name) and agents receive artifacts_dir directly in their prompt.
2. For **every** failed job across all releases and PRs, launch a separate **Agent** (using the `Agent` tool, NOT the `Skill` tool) with `subagent_type=microshift-ci:prow-job-analyzer`. For PR jobs, only launch agents for jobs with FAILURE status.

   **For release jobs:**

   ```text
   Agent: subagent_type=microshift-ci:prow-job-analyzer, prompt="Analyze this prow job:
   artifacts_dir: <ARTIFACTS_DIR>
   graphs_dir: <WORKDIR>/graphs/<JOB_ID>
   source_dir: <WORKDIR>/src/microshift-release-<RELEASE> (or <WORKDIR>/src/microshift for main)
   job_url: <JOB_URL>
   job_name: <JOB_NAME>"
   ```

   **For PR jobs:**

   ```text
   Agent: subagent_type=microshift-ci:prow-job-analyzer, prompt="Analyze this prow job:
   artifacts_dir: <ARTIFACTS_DIR>
   graphs_dir: <WORKDIR>/graphs/<BUILD_ID>
   source_dir: <WORKDIR>/src/microshift
   job_url: <JOB_URL>
   job_name: <JOB_NAME>"
   ```

   Substitute `<ARTIFACTS_DIR>`, `<JOB_ID>`/`<BUILD_ID>`, `<RELEASE>`, `<JOB_URL>`, and `<JOB_NAME>` from the prepare script's JSON output (`artifacts_dir`, `build_id`, `release`, `url`, `job` fields). Only include `graphs_dir` and `source_dir` if those directories exist.

   After each agent completes, save its JSON response to the corresponding file using the Write tool:
   - Release jobs: `<WORKDIR>/jobs/release-<RELEASE>-job-<N>-<JOB_ID>.json`
   - PR jobs: `<WORKDIR>/jobs/prs-job-<N>-pr<PR>-<JOB_NAME_SUFFIX>.json`

3. Launch **ALL** agents (all releases + PRs) in a **single message** as **foreground** agents (do NOT use `run_in_background`). Foreground agents in the same message run concurrently — this is just as fast as background agents but keeps your turn active until all complete.
4. Say "Analyzing N jobs in parallel..." in your message text alongside the Agent tool calls.
5. When all agents return, immediately proceed to Step 3 in the same turn. Do NOT stop or end your turn between Step 2 and Step 3.

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
- `pcp-export-pcp2json` — for PCP metric extraction

## Related Skills

- **microshift-ci:prow-job-analyzer** agent: Root cause analysis for a single job (used by Step 2 agents)
- **microshift-ci:create-bugs**: Bug correlation and creation (used in Step 3; can also be run with `--create` after this command)
- **microshift-ci:doctor-refresh**: Regenerate the HTML report from existing data (e.g., after `/microshift-ci:create-bugs --create`)

## Notes

- **Deterministic scripts** handle: data collection, artifact download, aggregation, HTML generation
- **LLM agents** handle: per-job root cause analysis (Step 2), Jira bug search and open bugs query (Step 3)
- `/microshift-ci:doctor-refresh` regenerates the HTML report from existing data. Use it after `/microshift-ci:create-bugs --create` to include newly created bugs
- Step 2 agents (per-job analysis) are launched in a single parallel wave
- Step 3 uses a single create-bugs agent with all sources (releases + rebase) comma-separated
- The `prepare` script downloads all artifacts upfront so prow-job agents use local paths (no redundant downloads)
- The `prepare` script also clones the MicroShift source to `<WORKDIR>/src/microshift` with per-release worktrees (`--repo openshift/microshift`); clone failure is non-fatal — agents record the absence in `analysis_gaps` and proceed
- The `finalize` script runs aggregation and HTML generation in one call
- All intermediate files use prescribed filenames in `<WORKDIR>` subdirectories (`jobs/`, `bugs/`) — no improvised names
- The HTML report is self-contained (no external CSS/JS dependencies)
- If a release analysis fails, it is noted in the report but does not block other releases
- If no rebase PRs are open, the Pull Requests tab shows "No open rebase pull requests found"
