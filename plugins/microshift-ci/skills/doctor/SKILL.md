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
- `$ARGUMENTS` (required): Comma-separated list of release versions (e.g., `4.19,4.20,4.21,4.22`)

## Scripts Directory

All scripts are run relative to the repository root:
```bash
SCRIPTS_DIR=plugins/microshift-ci/scripts
```

## Work Directory

Set once at the start and reference throughout:
```bash
WORKDIR=/tmp/microshift-ci-claude-workdir.$(date +%y%m%d)
```

## Implementation Steps

### Step 1: Prepare — Collect and Download All Artifacts

**Goal**: Deterministically collect all failed jobs and download their artifacts before any LLM analysis.

**Actions**:
1. Determine today's WORKDIR path by running `date +%y%m%d` and substituting into `/tmp/microshift-ci-claude-workdir.YYMMDD`. Use this value in all subsequent `--workdir` arguments.
2. Run the prepare script:
   ```bash
   bash ${SCRIPTS_DIR}/doctor.sh prepare --workdir ${WORKDIR} $ARGUMENTS --rebase
   ```
3. The script deterministically:
   - For each release: fetches failed periodic jobs, downloads artifacts, writes `${WORKDIR}/analyze-ci-release-<version>-jobs.json`
   - For rebase PRs: fetches PRs with failures, downloads artifacts, writes `${WORKDIR}/analyze-ci-prs-jobs.json` and `${WORKDIR}/analyze-ci-prs-status.json`
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
- If `$ARGUMENTS` is empty, show usage and stop
- If a release has no failed jobs, its jobs JSON will be an empty array — skip analysis for that release

### Step 2: Analyze Each Job Using /microshift-ci:prow-job

**Goal**: Get detailed root cause analysis for each failed job using pre-downloaded artifacts.

**Actions**:
1. Use the JSON summary output from Step 1 to build agent prompts. Do NOT read the job JSON files into the main conversation — the prepare script already printed all job details (artifacts_dir, build_id, job name) and agents receive artifacts_dir directly in their prompt.
2. For **every** failed job across all releases and PRs, launch a separate **Agent** (using the `Agent` tool, NOT the `Skill` tool). For PR jobs, only launch agents for jobs with FAILURE status.

   **For release jobs:**
   ```text
   Agent: subagent_type=general_purpose, prompt="Analyze this Prow job and save the report:
   1. Run /microshift-ci:prow-job <ARTIFACTS_DIR>
   2. After the analysis completes, save the FULL report output (including the --- STRUCTURED SUMMARY --- block) to:
      ${WORKDIR}/analyze-ci-release-<RELEASE>-job-<N>-<JOB_ID>.txt
      Use the Write tool to save the file. The file must contain the complete analysis report."
   ```

   **For PR jobs:**
   ```text
   Agent: subagent_type=general_purpose, prompt="Analyze this Prow job and save the report:
   1. Run /microshift-ci:prow-job <ARTIFACTS_DIR>
   2. After the analysis completes, save the FULL report output (including the --- STRUCTURED SUMMARY --- block) to:
      ${WORKDIR}/analyze-ci-prs-job-<N>-pr<PR>-<JOB_NAME_SUFFIX>.txt
      Use the Write tool to save the file. The file must contain the complete analysis report."
   ```

3. Launch **ALL** agents (all releases + PRs) in a single message using `run_in_background: true`
4. After launching, say "Analyzing N jobs in parallel..." and STOP.
5. As agent completion notifications arrive, respond with only "." (a single period) — no summaries, no status updates.
6. Only after ALL agents are confirmed complete, produce a single brief count and proceed to Step 3.

### Step 3: Run Bug Correlation (Dry-Run)

**Goal**: Search Jira for existing bugs matching each failure.

**Actions**:
1. **IMPORTANT**: Wait until ALL analysis agents from Step 2 are confirmed complete
2. For each release version, launch `microshift-ci:create-bugs` in dry-run mode as an **Agent**:
   ```text
   Agent: subagent_type=general_purpose, prompt="Run /microshift-ci:create-bugs <version>"
   ```
3. If rebase PR analysis produced job files, also launch `microshift-ci:create-bugs` for rebase PRs (check the PR jobs JSON to identify rebase PR source identifiers like `rebase-release-4.22`):
   ```text
   Agent: subagent_type=general_purpose, prompt="Run /microshift-ci:create-bugs rebase-release-<version>"
   ```
4. Launch all create-bugs agents **in parallel** with `run_in_background: true`
5. Respond with only "." for each intermediate completion notification. Wait until all complete.
6. Each agent produces `${WORKDIR}/analyze-ci-bugs-<source>.json`

**Error Handling**:
- If create-bugs fails for a release, note the failure but do not block other releases or HTML generation

### Step 4: Finalize — Aggregate and Generate HTML Report

**Goal**: Deterministically aggregate results and generate the HTML report.

**Actions**:
1. Run the finalize script:
   ```bash
   bash ${SCRIPTS_DIR}/doctor.sh finalize --workdir ${WORKDIR} $ARGUMENTS
   ```
2. The script deterministically:
   - Runs `aggregate.py` for each release and for PRs → `summary.json` files
   - Runs `create-report.py` → `microshift-ci-doctor-report.html`
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
    Release 4.20: 7 failed periodic jobs
    Release 4.21: 0 failed periodic jobs
    Release 4.22: 12 failed periodic jobs
  Pull Requests:
    2 rebase PRs with 5 total failed jobs

HTML report generated: ${WORKDIR}/microshift-ci-doctor-report.html
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

## Related Skills

- **microshift-ci:prow-job**: Single job analysis (used by Step 2 agents)
- **microshift-ci:create-bugs**: Bug correlation and creation (used in Step 3; can also be run with `--create` after this command)

## Notes
- **Deterministic scripts** handle: data collection, artifact download, aggregation, HTML generation
- **LLM agents** handle: per-job root cause analysis (Step 2), Jira bug search (Step 3)
- All agents (all releases + PRs) are launched in a single parallel wave — no per-release agents
- The `prepare` script downloads all artifacts upfront so prow-job agents use local paths (no redundant downloads)
- The `finalize` script runs aggregation and HTML generation in one call
- All intermediate files use prescribed filenames in `${WORKDIR}` — no improvised names
- The HTML report is self-contained (no external CSS/JS dependencies)
- If a release analysis fails, it is noted in the report but does not block other releases
- If no rebase PRs are open, the Pull Requests tab shows "No open rebase pull requests found"
