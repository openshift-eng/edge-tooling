---
name: edge-ocp-ci:generate-dashboard
description: "Edge OCP Payload Monitor — monitor OpenShift nightly payloads for edge topology (SNO/TNA/TNF) failures with AI-enriched analysis"
argument-hint: "[--versions 4.18,4.19,4.20,4.21,4.22,4.23,5.0] [--payloads N] [--skip-prow] [--skip-sippy] [--with-timing]"
user-invocable: true
---

# Edge OCP Payload Monitor Skill

You are helping a developer monitor OpenShift nightly payload health for edge topologies (SNO, TNA, TNF). This skill orchestrates the `payload-monitor` Python tool and existing marketplace CI skills to generate an interactive HTML dashboard report with AI-powered root cause analysis for blocking job failures.

## Existing Skills Reference

This skill composes with the following installed marketplace CI skills from the [ai-helpers](https://github.com/openshift-eng/ai-helpers) repository. These are used automatically for blocking job analysis:

### Data Fetching Skills

| Skill | When to Use |
|-------|-------------|
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
| `ci:analyze-payload` | Full payload analysis with historical lookback and HTML report — use for rejected/failing payloads with edge blockers |
| `ci:prow-job-analyze-test-failure` | Analyze failed tests by inspecting test code, downloading artifacts, and optionally integrating must-gather — use for any failing edge test |
| `ci:prow-job-analyze-install-failure` | Analyze OpenShift install failures from installer logs, log bundles, and sosreports — use when edge jobs fail at install stage |
| `ci:prow-job-analyze-metal-install-failure` | Analyze bare metal install failures using dev-scripts artifacts — use for metal/baremetal SNO or TNF jobs with "metal" in name |
| `ci:prow-job-analyze-resource` | Analyze K8s resource lifecycle in Prow job artifacts (audit logs, pod logs) — use when failure involves resource state issues |
| `ci:prow-job-artifact-search` | Search, list, and fetch artifacts from Prow job runs in GCS — use when you need to find specific artifacts |
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

**Important:** If a report with the same filename already exists, the tool automatically appends a timestamp (e.g., `report-2026-03-25-143027.html`). Capture the actual output path from the tool's log line:

- `Report: /path/to/report-{name}.html`

Use this actual path (not the hardcoded date-based name) in all subsequent steps.

The tool outputs:

- An HTML report (self-contained interactive dashboard)
- Blocking job summary printed to stdout (pipe-delimited lines between `BLOCKING_JOBS_START` and `BLOCKING_JOBS_END` markers)

### Step 4: Parse Blocking Jobs from Output and Analyze Failures

Parse the blocking job lines from the tool's stdout. Each line between the markers has the format:

```text
BLOCKING|job_name|prow_url|topology|version|payload_tag|prev_url1;prev_url2
```

The 7th field contains semicolon-separated Prow URLs for previous failed attempts (empty if no retries). Split on `;` to get individual URLs.

If no `BLOCKING_JOBS_START` marker appears in the output, there are no blocking failures — skip to Step 6.

**For informing job failures:** Do NOT run deep analysis. The HTML report already includes a suggestion to use Claude directly with `/ci:prow-job-analyze-test-failure <prow-url>`.

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

Then based on failure type of the latest attempt:
- If install failure (error contains "install should succeed", "bootstrap", or failed in pre/setup phase):
  Use `ci:prow-job-analyze-install-failure` (or `ci:prow-job-analyze-metal-install-failure` if job name contains "metal")
- If test failure (job passed install but failed during test phase):
  Use `ci:prow-job-analyze-test-failure`
- If resource/state failure (etcd issues, operator degraded, node not ready):
  Use `ci:prow-job-analyze-resource`

For JIRA context: use `ci:fetch-jira-issue` for any linked bugs.

Return ONLY a JSON object with these fields:
{
  "prow_url": "{prow_url}",
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
     "root_cause": "Analysis timed out or failed: {error_message}",
     "failure_type": "Analysis error",
     "impact": "Unable to determine — manual investigation needed",
     "suspect_prs": [],
     "recommendation": "Run /ci:prow-job-analyze-test-failure {prow_url} manually for detailed analysis",
     "same_root_cause": true,
     "attempt_analyses": []
   }
   ```

2. Continue with results from other subagents — do NOT discard partial results.
3. Include a note in the final output indicating which jobs could not be analyzed.

### Step 5: Write Analysis File

Collect the deep analysis results (from subagents or inline analysis) and write a small analysis-only JSON file keyed by `prow_url`. Use the actual report stem from Step 3 (e.g., `reports/analysis-2026-03-25.json` or `reports/analysis-2026-03-25-143027.json`):

```json
{
  "by_prow_url": {
    "https://prow.ci.openshift.org/view/gs/.../123": {
      "root_cause": "Overall root cause synthesized across all attempts",
      "failure_type": "Infrastructure flake | Test regression | Install failure | Platform issue",
      "impact": "How this affects payload acceptance and which topologies",
      "suspect_prs": ["https://github.com/org/repo/pull/123"],
      "recommendation": "Specific next action (file bug, wait for fix, investigate PR, etc.)",
      "same_root_cause": true,
      "attempt_analyses": []
    }
  }
}
```

When `same_root_cause` is false, `attempt_analyses` contains per-attempt breakdown with `prow_url`, `root_cause`, and `failure_type` for each attempt. When true or when there is only one attempt, `attempt_analyses` can be empty.

This file is intentionally small — it contains only the AI analysis results, not the full report data. This minimizes token usage.

### Step 6: Patch Analysis into HTML

Patch the analysis directly into the existing HTML report. Use the actual report path from Step 3:

```bash
cd "$TOOL_DIR" && .venv/bin/python -m payload_monitor \
  --merge-analysis reports/<actual-analysis>.json \
  --output reports/<actual-report>.html
```

This finds each job's detail section by its prow URL and injects the "AI Root Cause Analysis" card directly into the HTML. No JSON round-trip needed.

If there were no blocking failures (no analysis file), skip this step entirely — the HTML report is already complete.

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

Analyzed {N} blocking job failure(s) with AI root cause analysis.
Open the HTML report for the full interactive dashboard with findings summary, suggested actions, and detailed analysis.
```

Use the actual report file path captured from the tool's `Report:` log line in Step 3 — do NOT hardcode a date-based path.

Offer follow-up actions the user can take from this session:

- **Create JIRA bugs** for untracked failures
- **Set release blocker** on a JIRA issue (`ci:set-release-blocker`)
- **Triage a regression** in Component Readiness (`ci:triage-regression`)
- **Trigger payload job** to test a fix (`ci:trigger-payload-job`)
- **Investigate an informing job** further (`ci:prow-job-analyze-test-failure`)

---

## Important Notes

- The Python tool must be run from the `$TOOL_DIR` directory
- Dependencies are checked and installed automatically in Step 2
- JIRA features require a `JIRA_TOKEN` environment variable with **read-only** permissions — the tool only searches for existing bugs, never creates or modifies issues
- Prow artifact fetching requires `gsutil` (Google Cloud SDK)
- Do NOT modify the Python source code — this skill is an orchestration layer on top
- Do NOT duplicate report data in your output — the HTML dashboard is the primary output, keep your response brief
- Deep analysis runs automatically for **blocking jobs only** — informing jobs get a Claude suggestion instead
- Prioritize blocking failures over informing failures in all analysis
- The dashboard automatically detects **recurring** (2+ payloads) and **persistent** (3+ payloads) failures — highlight these in your summary
- **Unstable** jobs are informing jobs failing in 3+ consecutive payloads — these are consistently failing and need attention
- **Cross-topology correlation** ("Also in: SNO/TNA/TNF" hints) surfaces shared platform issues — when you see these, investigate the shared root cause rather than each topology independently
- Every finding in the dashboard has a next step (JIRA link, Claude command, triage URL, or create-bug button) — reference these in your follow-up suggestions
- For TNF/TNA failures, pay special attention to etcd, Pacemaker, and fencing-related errors
- For SNO failures, check for single-node-specific issues like workload partitioning, resource constraints
- When multiple edge topologies fail in the same payload, investigate whether it's a shared platform issue vs topology-specific
