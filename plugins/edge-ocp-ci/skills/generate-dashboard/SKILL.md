---
name: edge-ocp-ci:generate-dashboard
description: "Edge OCP Payload Monitor — monitor OpenShift nightly payloads for edge topology (SNO/TNA/TNF) failures with AI-enriched analysis"
argument-hint: "[--versions 4.18,4.19,4.20,4.21,4.22,4.23,5.0] [--payloads N] [--skip-prow] [--skip-sippy] [--with-timing]"
user-invocable: true
---

# Edge OCP Payload Monitor Skill

You are helping a developer monitor OpenShift nightly payload health for edge topologies (SNO, TNA, TNF). This skill orchestrates the `payload-monitor` Python tool and existing marketplace CI skills to generate an interactive HTML dashboard report with AI-powered root cause analysis for blocking job failures and the worst five Sippy job-health findings.

## Existing Skills Reference

This skill composes with the following installed marketplace CI skills from the [ai-helpers](https://github.com/openshift-eng/ai-helpers) repository. These are used automatically for blocking and selected Sippy job analysis:

### Data Fetching Skills

| Skill | When to Use |
|-------|-------------|
| `ci:ask-sippy` | Investigate an exact job name and release, its pass-rate history, and recent failed Prow runs |
| `ci:fetch-payloads` | Fetch recent release payloads from the release controller — use as a cross-check or when the Python tool's data needs supplementation |
| `ci:fetch-releases` | Fetch available OpenShift releases from Sippy — use to auto-discover active streams |
| `ci:fetch-test-report` | Fetch test report from Sippy with pass rates, test ID, and Jira component — use for per-test regression detail |
| `ci:fetch-job-run-summary` | Fetch a Prow job run summary with all failed tests grouped by SIG — use to understand failure scope |
| `ci:fetch-prowjob-json` | Fetch key data from a Prow job's prowjob.json artifact — use for job metadata |
| `ci:fetch-regression-details` | Fetch detailed Component Readiness regression info from Sippy — use for Sippy regressions |
| `ci:fetch-related-triages` | Fetch existing triages and untriaged regressions related to a given regression — use to avoid duplicate work |
| `ci:fetch-jira-issue` | Fetch JIRA issue details including status, assignee, comments, and progress classification — use for enriched bug context |
| `ci:fetch-test-runs` | Fetch test runs from Sippy including outputs for AI similarity analysis — use to compare failure patterns |

### Deep Analysis Skills

| Skill | When to Use |
|-------|-------------|
| `ci:prow-job-analysis` | General-purpose Prow job failure analysis — classifies job type and failure mode, then routes internally to the right investigation procedure (install, test, upgrade, resource exhaustion, networking, etc.) — use for any failing edge job |
| `ci:prow-job-analyze-resource` | Analyze K8s resource lifecycle in Prow job artifacts (audit logs, pod logs) — use for a deep resource-specific audit trail beyond what `ci:prow-job-analysis` covers |
| `ci:analyze-regression` | Analyze Component Readiness regression details and suggest next steps — use for Sippy-detected regressions |
| `ci:check-if-jira-regression-is-ongoing` | Check if a JIRA regression bug is still ongoing or resolved — use to validate whether known bugs still apply |

### Action Skills

| Skill | When to Use |
|-------|-------------|
| `ci:trigger-payload-job` | Trigger payload testing on a PR — use to verify a fix resolves the payload failure |

---

## Workflow

### Step 1: Parse Arguments

Parse `$ARGUMENTS` to determine options:

- **`--versions X,Y,Z`**: Override which OCP versions to monitor (e.g., `--versions 4.18,4.19`)
- **`--payloads N`**: Number of payloads to analyze per stream (1-10, default 5)
- **`--skip-prow`**: Skip Prow artifact fetching (faster, less detail)
- **`--skip-sippy`**: Skip Sippy regression check
- **`--with-timing`**: Include install/upgrade timing insights (disabled by default)
- If `$ARGUMENTS` is empty: use defaults (all configured versions)

### Step 2: Install Prerequisites (if needed)

The tool uses a virtual environment at `$TOOL_DIR/.venv`. Create it and install dependencies if not already present:

PLUGIN_DIR is the directory containing this skill file (i.e., plugins/edge-ocp-ci/skills/generate-dashboard).

TOOL_DIR="$(git -C "$PLUGIN_DIR" rev-parse --show-toplevel)/payload-monitor"

```bash
cd "$TOOL_DIR" && (test -d .venv || python3 -m venv .venv) && .venv/bin/python -c "import requests, jinja2, click" 2>/dev/null || .venv/bin/pip install -r requirements.txt
```

This avoids re-creating the venv or re-running `pip install` on every invocation when the environment is already set up.

### Step 3: Run the Python Tool

Run the payload monitor Python tool to collect data and generate the base report:

```bash
cd "$TOOL_DIR" && .venv/bin/python -m payload_monitor --output reports/report-$(date +%Y-%m-%d).html [OPTIONS]
```

Pass through any relevant flags (`--versions`, `--payloads`, `--skip-prow`, `--skip-sippy`, `--with-timing`).

**Timeout guidance:** When using `--with-timing`, set the Bash tool timeout to at least **10 minutes** (600000 ms). On a cold start (first run after deployment, or if the previous run's cache artifact is unavailable), the timing collector may need to backfill up to 7 days of run data. Subsequent warm runs with a seeded cache typically complete in under 2 minutes.

**Important:** If a report with the same filename already exists, the tool automatically appends a timestamp (e.g., `report-2026-03-25-143027.html`). Capture the actual output path from the tool's log line:

- `Report: /path/to/report-{name}.html`

Use this actual path (not the hardcoded date-based name) in all subsequent steps.

The tool outputs:

- An HTML report (self-contained interactive dashboard)
- `<actual-report-stem>.ai-targets.json` beside the HTML, containing `sippy_jobs`: the exact (up to five) job/release pairs shown in Findings Summary, their rates, Sippy links, stable `analysis_key` values, and any known failing Prow runs
- Blocking job summary printed to stdout (pipe-delimited lines between `BLOCKING_JOBS_START` and `BLOCKING_JOBS_END` markers)

### Step 4: Analyze Blocking Failures and Selected Sippy Jobs

Parse the blocking job lines from the tool's stdout. Each line between the markers has the format:

```text
BLOCKING|job_name|prow_url|topology|version|payload_tag|prev_url1;prev_url2
```

The 7th field contains semicolon-separated Prow URLs for previous failed attempts (empty if no retries). Split on `;` to get individual URLs.

Read `<actual-report-stem>.ai-targets.json` from Step 3 as well as the blocking markers. Do not recompute or substitute the top five: analyze every entry in `sippy_jobs`, including flat or improving jobs below 50% and jobs absent from the sampled payloads.

If no `BLOCKING_JOBS_START` marker appears, skip only blocking analysis and continue with Sippy analysis below. Skip Step 5 and Step 6 only when both the blocking list and `sippy_jobs` are empty. A missing or malformed targets file is an error to investigate, not an empty list.

**For informing job failures:** Run deep analysis when the job is among the selected Sippy targets. Other informing jobs retain the existing Claude suggestion.

#### Analysis Prompt

Use the following prompt for each blocking job. When there is **exactly 1 blocking failure**, run it directly in the main agent (no subagent). When there are **2 or more blocking failures**, spawn one subagent per job using the Agent tool, all in parallel — this significantly reduces wall-clock time.

```text
Analyze this failing blocking edge job and return a JSON deep_analysis object.

Job: {job_name}
Prow URL (latest attempt): {prow_url}
Previous Attempt URLs: {prev_url1}, {prev_url2} (or "none" if no previous attempts)
Topology: {topology}
Version: {version}
Payload: {payload_tag}

Steps:
1. Use `ci:fetch-job-run-summary` with EACH Prow URL (latest + all previous attempts) to get failed tests grouped by SIG for each attempt
2. Use `ci:fetch-prowjob-json` with the latest Prow URL to get job metadata
3. Compare the failure patterns across attempts:
   - Are the same tests failing with the same errors?
   - Are different tests failing or different error messages?
   - Does the failure mode change between attempts?

4. Use `ci:prow-job-analysis` on the latest attempt's Prow URL for root cause classification and analysis — it auto-detects job type and failure mode and routes internally to the right investigation procedure
5. If the failure is specifically about a Kubernetes resource's lifecycle (created/modified/deleted at an unexpected time) and needs an audit-log timeline beyond what `ci:prow-job-analysis` surfaced, use `ci:prow-job-analyze-resource` as a follow-up

For JIRA context: use `ci:fetch-jira-issue` for any linked bugs.

Return ONLY a JSON object with these fields:
{
  "prow_url": "{prow_url}",
  "job_name": "{job_name}",
  "root_cause": "Overall root cause (synthesize across all attempts if same cause, or describe the dominant pattern)",
  "failure_type": "Infrastructure flake | Test regression | Install failure | Platform issue",
  "impact": "How this affects payload acceptance and which topologies",
  "suspect_prs": ["https://github.com/org/repo/pull/123"],
  "recommendation": "Specific next action",
  "same_root_cause": true or false,
  "attempt_analyses": [
    {
      "prow_url": "attempt_1_url",
      "root_cause": "Root cause for this attempt",
      "failure_type": "Infrastructure flake"
    }
  ]
}

Rules for same_root_cause and attempt_analyses:
- Set same_root_cause to true if all attempts fail for the same fundamental reason (same tests, same errors, same infrastructure issue)
- Set same_root_cause to false if attempts fail for different reasons (different tests failing, different error types)
- When same_root_cause is true, attempt_analyses can be omitted or empty
- When same_root_cause is false, attempt_analyses MUST contain one entry per attempt (previous + latest), ordered by attempt number
- If there are NO previous attempts (single attempt), omit attempt_analyses and set same_root_cause to true
```

When using subagents, launch all in parallel using multiple Agent tool calls in the same response. Collect their results and proceed to Step 5.

**Subagent configuration:** When spawning subagents, set a timeout of 5 minutes per agent. If a subagent times out or returns an error:

1. Record the failure in the analysis JSON with a descriptive error:

   ```json
   {
     "prow_url": "{prow_url}",
     "job_name": "{job_name}",
     "root_cause": "Analysis timed out or failed: {error_message}",
     "failure_type": "Analysis error",
     "impact": "Unable to determine — manual investigation needed",
     "suspect_prs": [],
     "recommendation": "Run /ci:prow-job-analysis {prow_url} manually for detailed analysis",
     "same_root_cause": true,
     "attempt_analyses": []
   }
   ```

2. Continue with results from other subagents — do NOT discard partial results.
3. Include a note in the final output indicating which jobs could not be analyzed.

#### Sippy Job Analysis

After collecting blocking analyses, analyze every `sippy_jobs` entry. Reuse existing analysis for any identical Prow URL in `known_failing_runs`; do not run the same Prow investigation twice. Still produce a separate job-health synthesis keyed by `analysis_key`, scoped to that exact release. One Sippy target can be analyzed inline; independent remaining targets can use parallel subagents with the same five-minute timeout and error handling as blocking jobs.

For each target:

1. Use `ci:ask-sippy` with the exact `job_name`, `version`, and `triage_url` to investigate the current and previous periods, failed tests, and recent failed Prow runs. These are job-health findings, not Component Readiness test regressions; do not pass a job identifier to Component Readiness regression APIs.
2. Start with `known_failing_runs`, reusing blocking analyses for matching URLs. If no runs are listed or they do not explain the historical pass rate, find recent failed runs for this job and release through Sippy. Use `ci:fetch-job-run-summary` and `ci:prow-job-analysis` on representative failures; compare failure patterns and inspect linked JIRA bugs as for blocking jobs.
3. Return the same deep-analysis fields as the blocking prompt, plus `job_name`, under the target's exact `analysis_key`. Explain the evidence for the poor pass rate or relative drop, affected topology, and a specific next action. Historical poor health alone does not establish payload-blocking impact. For improving low-rate jobs, describe continued instability rather than inventing a new regression.
4. If evidence cannot be retrieved or analysis times out, return an explicit `Analysis error` entry for that key, state what evidence is missing, and provide a manual investigation recommendation using the target's Sippy link. Never invent a Prow URL or silently omit a target.

### Step 5: Write Analysis File

Collect the deep analysis results (from subagents or inline analysis) and write a small analysis-only JSON file with `by_prow_url` for blocking runs and `by_sippy_job` for the selected Sippy job/release keys. Use the actual report stem from Step 3 (e.g., `reports/analysis-2026-03-25.json` or `reports/analysis-2026-03-25-143027.json`):

```json
{
  "by_prow_url": {
    "https://prow.ci.openshift.org/view/gs/.../123": {
      "job_name": "periodic-ci-...",
      "root_cause": "Overall root cause synthesized across all attempts",
      "failure_type": "Infrastructure flake | Test regression | Install failure | Platform issue",
      "impact": "How this affects payload acceptance and which topologies",
      "suspect_prs": ["https://github.com/org/repo/pull/123"],
      "recommendation": "Specific next action (file bug, wait for fix, investigate PR, etc.)",
      "same_root_cause": true,
      "attempt_analyses": []
    }
  },
  "by_sippy_job": {
    "4.19/periodic-ci-example-sno": {
      "job_name": "periodic-ci-example-sno",
      "root_cause": "Evidence-based synthesis of recent failed runs and pass-rate history",
      "failure_type": "Test regression",
      "impact": "Historical SNO job instability on 4.19; payload blocking status is reported separately",
      "suspect_prs": [],
      "recommendation": "Specific next investigation or fix",
      "same_root_cause": true,
      "attempt_analyses": []
    }
  }
}
```

Copy Sippy keys exactly from the targets file; the example key is illustrative. Before patching, verify that the keys in `by_sippy_job` exactly match all `sippy_jobs[*].analysis_key` values. Fill any missing key with its completed analysis or an explicit analysis-error result. When there are only Sippy targets, `by_prow_url` can be empty. Do not skip the merge in that case.

When `same_root_cause` is false, `attempt_analyses` contains per-attempt breakdown with `prow_url`, `root_cause`, and `failure_type` for each attempt. When true or when there is only one attempt, `attempt_analyses` can be empty.

This file is intentionally small — it contains only the AI analysis results, not the full report data. This minimizes token usage.

### Step 6: Patch Analysis into HTML

Patch the analysis directly into the existing HTML report. Use the actual report path from Step 3:

```bash
cd "$TOOL_DIR" && .venv/bin/python -m payload_monitor \
  --merge-analysis reports/<actual-analysis>.json \
  --output reports/<actual-report>.html
```

This finds blocking job details by Prow URL and Sippy Job Health Status rows by their stable job/release key, then injects the same "AI Root Cause Analysis" cards. Both show compact Root Cause/Action cards that open the full analysis inside the matching job row on click or keyboard activation. Sippy jobs without AI analysis remain non-expandable; there is no separate selected-jobs analysis list. No JSON round-trip needed.

Skip this step only if there were neither blocking failures nor selected Sippy targets (no analysis file).

### Step 6b: Clean Up Analysis File

After successfully patching the analysis into the HTML report, delete the intermediate analysis JSON file:

```bash
rm "$TOOL_DIR/reports/<actual-analysis>.json"
```

This file has served its purpose — the analysis is now embedded in the HTML report.

### Step 7: Present Output

Do NOT duplicate the report data or findings summary — the HTML dashboard already contains all of that. Present only a brief confirmation:

```text
## Edge OCP Payload Monitor Report Generated

Report: `<actual report path captured from Step 3>`

Analyzed {N} blocking job failure(s) and {M} selected Sippy job(s) with AI root cause analysis. Report any analysis errors separately.
Open the HTML report for the full interactive dashboard with findings summary, suggested actions, and detailed analysis.
```

Use the actual report file path captured from the tool's `Report:` log line in Step 3 — do NOT hardcode a date-based path.

Offer follow-up actions the user can take from this session:

- **Create JIRA bugs** for untracked failures
- **Set release blocker** on a JIRA issue (`ci:set-release-blocker`)
- **Investigate a regression** in Component Readiness (`ci:ask-sippy`)
- **Trigger payload job** to test a fix (`ci:trigger-payload-job`)
- **Investigate an informing job** further (`ci:prow-job-analysis`)

---

## Important Notes

- The Python tool must be run from the `$TOOL_DIR` directory
- Dependencies are checked and installed automatically in Step 2
- Suggested-bug "Create Bug in JIRA" links require no authentication — they open a browser-based create form for manual review before submission
- Prow artifact fetching requires `gsutil` (Google Cloud SDK)
- Do NOT modify the Python source code — this skill is an orchestration layer on top
- Do NOT duplicate report data in your output — the HTML dashboard is the primary output, keep your response brief
- Deep analysis runs automatically for **blocking jobs and every selected Sippy target (up to five)** — other informing jobs get a Claude suggestion instead
- Prioritize blocking failures over informing failures in all analysis
- The dashboard automatically detects **recurring** (2+ payloads) and **persistent** (3+ payloads) failures — highlight these in your summary
- **Unstable** jobs are informing jobs failing in 3+ consecutive payloads — these are consistently failing and need attention
- **Cross-topology correlation** ("Also in: SNO/TNA/TNF" hints) surfaces shared platform issues — when you see these, investigate the shared root cause rather than each topology independently
- Every finding in the dashboard has a next step (JIRA link, Claude command, triage URL, or create-bug button) — reference these in your follow-up suggestions
- For TNF/TNA failures, pay special attention to etcd, Pacemaker, and fencing-related errors
- For SNO failures, check for single-node-specific issues like workload partitioning, resource constraints
- When multiple edge topologies fail in the same payload, investigate whether it's a shared platform issue vs topology-specific
