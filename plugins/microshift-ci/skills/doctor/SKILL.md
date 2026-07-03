---
name: microshift-ci:doctor
argument-hint: <release1,release2,...>
description: Analyze CI for multiple MicroShift releases and produce an HTML summary
user-invocable: true
allowed-tools: Bash, Read, Write, Glob, Grep, Agent
---

# microshift-ci:doctor

## Synopsis

```bash
/microshift-ci:doctor <release1,release2,...>
```

## Description

Accepts a comma-separated list of MicroShift release versions, runs analysis for each release and for open rebase PRs, and produces a single HTML summary file consolidating all results. Deterministic scripts handle data collection, artifact download, evidence extraction, failure grouping, aggregation, and HTML generation. LLM agents handle exactly two things: root cause analysis of each distinct failure group (Step 2) and Jira bug correlation (Step 3).

## Arguments

- `<ARGUMENTS>` (required): Comma-separated list of release versions (e.g., `4.19,4.20,4.21,4.22`)

## Work Directory

Compute once at the start by running `date +%y%m%d` and substituting into the path below. In all commands, replace `<WORKDIR>` with the computed path — do not use shell variables.

```text
/tmp/microshift-ci-claude-workdir.<YYMMDD>
```

## Implementation Steps

### Step 1: Prepare — Collect and Download All Artifacts

1. Determine today's `<WORKDIR>` (see above). Use this value in all subsequent commands.
2. Run the prepare script:

   ```text
   bash plugins/microshift-ci/scripts/doctor.sh prepare --component microshift --workdir <WORKDIR> <ARGUMENTS> --rebase --repo openshift/microshift
   ```

3. The script fetches failed periodic jobs per release and rebase PRs with failures, downloads all artifacts, clones the MicroShift source with per-release worktrees, and prints a JSON summary of releases, job counts, and file paths. Read that summary — do NOT read the job JSON files it references.

**Error Handling**:

- If `<ARGUMENTS>` is empty, show usage and stop
- A release with no failed jobs simply has nothing to analyze
- A release with an `"error"` field failed data collection — report it to the user but continue with other releases

### Step 1b: Generate PCP Performance Graphs

```text
bash plugins/microshift-ci/scripts/doctor.sh graphs --component microshift --workdir <WORKDIR>
```

Generates CPU/memory/disk graphs at `<WORKDIR>/graphs/<build_id>/` for jobs with PCP archives. If prerequisites are missing (`pcp2json`, `matplotlib`), the script errors and stops.

### Step 1c: Extract Structured Evidence

```text
bash plugins/microshift-ci/scripts/doctor.sh evidence --component microshift --workdir <WORKDIR>
```

Produces `<WORKDIR>/evidence/evidence-<BUILD_ID>.json` per job — the structured evidence packs (failed step, failure fingerprint, per-scenario alerts, sosreport paths) that analysis agents start from. If it fails for some jobs, note the errors and continue — agents fall back to raw artifacts.

### Step 1d: Plan Analysis Groups

1. Run the plan script:

   ```text
   bash plugins/microshift-ci/scripts/doctor.sh plan --component microshift --workdir <WORKDIR>
   ```

   It groups all failed jobs (releases + PRs) by failure fingerprint, writes template verdicts for pure-infrastructure and no-failure groups (no agent needed), and renders one fully substituted agent prompt file per remaining group. Its JSON summary's `agent_groups` array lists each group's `prompt_file` and `report_file`.

### Step 2: Analyze Each Group

1. For **every** entry in `agent_groups`, launch a separate **Agent** with exactly this prompt — the prompt files are fully pre-rendered, do NOT read or modify them yourself:

   ```text
   Read <PROMPT_FILE> and follow its instructions exactly.
   ```

2. Launch **ALL** group agents in a **single message** as **foreground** agents (do NOT use `run_in_background`). Foreground agents in the same message run concurrently — this is just as fast as background agents but keeps your turn active until all complete.
3. Say "Analyzing N failure groups (M jobs) in parallel..." in your message text alongside the Agent tool calls. If `agent_groups` is empty, skip directly to step 5 (fan-out).
4. When all agents return, **validate the group reports**:

   ```text
   python3 plugins/microshift-ci/scripts/validate-reports.py <WORKDIR>/jobs/analysis-group-*.txt
   ```

   If the script exits 0 (all pass), continue to step 5.

   If it exits 1, it prints a `--- VALIDATION FAILURES ---` block listing each failed file and its errors. For each failed file, launch a **fix agent**:

   ```text
   Agent: subagent_type=general_purpose, prompt="Fix citation errors in a CI analysis report.

   The report at <FAILED_FILE> has causal-chain links whose citations failed
   verification against the actual artifact files. The specific errors are:
   <PASTE ERRORS FOR THIS FILE FROM VALIDATION OUTPUT>

   Fix the report by RE-GROUNDING each flagged link in the real artifacts.
   The group's jobs, evidence packs, and artifacts directories are listed in
   <PROMPT_FILE>.
   1. Read the report at <FAILED_FILE> and the job list in <PROMPT_FILE>
   2. For each flagged link:
      - 'found at line N' → re-read that line in the cited file; if it supports
        the cause, update the citation to that line.
      - 'cited file not found' → Grep the quoted text under the group's
        artifacts directories and cite the file:line where it actually appears.
        The evidence packs have file and line fields for each extracted alert.
      - 'quote not found' → re-read the cited file around the cited line and
        replace the quote with the verbatim text that supports the cause.
   3. NEVER delete a link merely to pass validation. Only if a real search finds
      no supporting artifact: remove the link, add the unverified claim to
      analysis_gaps (e.g. "unverified: <cause>"), and downgrade confidence.
   4. Rewrite the corrected report (BOTH the human-readable Causal Chain section AND the
      STRUCTURED SUMMARY JSON causal_chain array) back to <FAILED_FILE>.
   5. Verify your fix: python3 plugins/microshift-ci/scripts/validate-reports.py <FAILED_FILE>
      must print OK. Iterate until it does.
   6. Reply with EXACTLY: FIXED <FAILED_FILE>"
   ```

   Launch all fix agents in a single message (parallel). Then continue to step 5.

5. **Fan out** the group reports into the per-job report files consumed by aggregation and bug correlation:

   ```text
   bash plugins/microshift-ci/scripts/doctor.sh fanout --component microshift --workdir <WORKDIR>
   ```

6. Proceed to Step 3. Do NOT stop or end your turn between Step 2 and Step 3.

### Step 3: Run Bug Correlation (Dry-Run)

1. Build the source list: all release versions from `<ARGUMENTS>` plus any rebase PR source identifiers from the PR jobs (e.g., `rebase-release-4.22`).
2. Launch a **single** `microshift-ci:create-bugs` **foreground** agent in dry-run mode with all sources:

   ```text
   Agent: subagent_type=general_purpose, prompt="Run /microshift-ci:create-bugs <all-sources-comma-separated>"
   ```

   It produces `<WORKDIR>/bugs/bug-matches-<source>.json` per source and `<WORKDIR>/report-create-bugs.txt`.
3. When the agent returns, immediately proceed to Step 4 in the same turn. Do NOT stop or end your turn between Step 3 and Step 4. If create-bugs fails, note the failure but do not block HTML generation.

### Step 4: Finalize — Aggregate and Generate HTML Report

**IMPORTANT**: This step is MANDATORY. The task is incomplete without it. You MUST run this even if previous steps produced errors.

```text
bash plugins/microshift-ci/scripts/doctor.sh finalize --component microshift --workdir <WORKDIR> <ARGUMENTS>
```

Aggregates per-release and PR summaries and generates `report-microshift-ci-doctor.html`.

### Step 5: Report Completion

Display the path to the generated HTML file and summarize: failed job counts per release, analysis groups (agents vs deterministic), rebase PR status, and bug correlation results.

## Example

```bash
/microshift-ci:doctor 4.19,4.20,4.21,4.22
```

## Prerequisites

- `gsutil` CLI must be installed for GCS access (uses anonymous access on public buckets)
- `gh` CLI must be authenticated with access to openshift/microshift
- MCP Jira server must be configured (for bug correlation)
- Internet access to fetch job data from Prow/GCS
- Bash shell, Python 3
- `pcp-export-pcp2json` and `matplotlib` — for PCP graph generation

## Related Skills and Agents

- **agents/analyze-evidence.md**: Evidence-aware group analysis agent template (rendered per group by the Step 1d plan script; spawned in Step 2)
- **microshift-ci:prow-job**: Standalone job analysis from URL or artifacts directory (for manual use)
- **microshift-ci:create-bugs**: Bug correlation and creation (used in Step 3; can also be run with `--create` after this command)
- **microshift-ci:doctor-refresh**: Regenerate the HTML report from existing data (e.g., after `/microshift-ci:create-bugs --create`)

## Notes

- One agent analyzes each distinct failure fingerprint (not each job); pure-infrastructure and no-failure groups are resolved by script with no agent at all
- All intermediate files use prescribed filenames in `<WORKDIR>` subdirectories (`jobs/`, `bugs/`, `evidence/`, `prompts/`) — no improvised names
- The HTML report is self-contained (no external CSS/JS dependencies)
- If a release analysis fails, it is noted in the report but does not block other releases
