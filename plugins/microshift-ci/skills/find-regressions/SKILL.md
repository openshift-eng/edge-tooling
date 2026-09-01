---
name: microshift-ci:find-regressions
argument-hint: <source1>[,<source2>,...]
description: Search JIRA for pre-existing bugs and draft ticket suggestions for unmatched CI failures
user-invocable: true
allowed-tools: Bash, Read, Write, Glob, Grep, Agent, mcp__jira__jira_search, mcp__jira__jira_get_issue
---

# microshift-ci:find-regressions

## Synopsis

```bash
/microshift-ci:find-regressions <source>
/microshift-ci:find-regressions <source1>,<source2>,...
```

## Description

Reads individual job analysis reports produced by `microshift-ci:doctor` and searches JIRA for pre-existing bugs matching CI test failures. For failures with no existing bugs, drafts ticket suggestions that are consumed by the HTML report's "Create Bug in JIRA" button for manual filing.

This command does NOT create or update JIRA issues. It produces:

- **Bug mapping files** consumed by `create-report.py` to show linked bugs and render "Create Bug in JIRA" buttons in the HTML report
- **Merged candidates JSON** consumed by `fix-test-bugs` and other downstream skills
- **Results JSON** categorizing each failure as `suggest`, `linked`, or `skip`
- **Text report** summarizing the categorization for human review

Candidates are always **fuzzy-matched across sources** using token-based overlap similarity (50% threshold) with step-name bucketing — the same root cause appearing in multiple releases becomes a single candidate and a single entry referencing all affected releases.

This command does NOT re-analyze CI jobs. It consumes existing job analysis files from `<WORKDIR>`.

## Arguments

- `<ARGUMENTS>` (required): One or more comma-separated sources. Each source is one of:
  - **Release version** (e.g., `4.22`, `main`): Looks for files matching `jobs/release-<release>-job-*.json`
  - **PR number** (e.g., `pr-6396` or `pr6396`): Looks for files matching `jobs/prs-job-*-pr<number>-*.json`
  - **Rebase PR shorthand** (e.g., `rebase-release-4.22`): Resolves to the corresponding rebase PR by scanning existing `jobs/prs-job-*` files for the matching release version in their content

## Prerequisites

- Job analysis files must already exist in `<WORKDIR>/jobs/`:
  - For releases: `jobs/release-<release>-job-*.json` (produced by `/microshift-ci:doctor`)
  - For PRs: `jobs/prs-job-*-pr<number>-*.json` (produced by `/microshift-ci:doctor`)
- Each job file must be a valid JSON array (see below)
- MCP Jira server must be configured and accessible

### Job File Format

Each job analysis file produced by the `microshift-ci:prow-job-analyzer` agent is a pure JSON file containing an array with one object per independent failure. Each file may contain multiple entries when a job has independent failures across different scenarios.

```json
[
  {
    "severity": 3,
    "stack_layer": "test",
    "step_name": "openshift-microshift-e2e-metal-tests",
    "error_signature": "concise, unique description of the root cause error",
    "root_cause": "one-line description of WHY the failure happened",
    "raw_error": "verbatim primary error message from logs",
    "infrastructure_failure": false,
    "job_url": "full prow job URL",
    "job_name": "full periodic job name",
    "release": "4.22",
    "remediation": "suggested fix or next step",
    "finished": "2026-06-01"
  }
]
```

If a job file cannot be parsed as valid JSON, it is skipped with a warning.

## Work Directory

Compute once at the start by running `date +%y%m%d` and substituting into the path below. In all commands, replace `<WORKDIR>` with the computed path — do not use shell variables.

```text
/tmp/microshift-ci-claude-workdir.<YYMMDD>
```

## Implementation Steps

### Step 1: Prepare Bug Candidates (Deterministic Script)

**Actions**:

1. Parse `<ARGUMENTS>` to extract source(s)
2. Split sources on commas to get `SOURCES` list (e.g., `["4.22"]` or `["4.20", "4.21", "4.22", "5.0", "main"]`)
3. Compute `SOURCE_TAG` — a short identifier used in per-run output filenames (merged candidates, results, report). Use the **first source** in the list (e.g., `4.22`, `main`, `rebase-release-4.22`). Do NOT concatenate all sources.
4. Determine today's WORKDIR path by running `date +%y%m%d` and substituting into `/tmp/microshift-ci-claude-workdir.<YYMMDD>`. Run `mkdir -p` on it. Run `mkdir -p <WORKDIR>/bugs`.
5. For **each source** in `SOURCES`, run the preparation script:

   ```text
   python3 plugins/microshift-ci/scripts/search-bugs.py <source> --workdir <WORKDIR>
   ```

   Each invocation writes `<WORKDIR>/bugs/bug-candidates-<source>.json` containing parsed and deduplicated bug candidates with pre-computed `keywords`, `test_ids`, `jobs[]`, and `remediation`.

**Error Handling**:

- No arguments: show usage and stop
- Script exits with error if no job files found — relay its error message to the user

### Step 1a: Check for Cached Jira Results

After loading per-source candidates (Step 1), check whether bug mapping files already exist for ALL sources. These files are written by Step 2 on every run and contain the Jira search results (`duplicates[]`, `regressions[]`). If they exist and cover all per-source candidates, Step 2 can be skipped entirely.

**Actions**:

1. For each source in `SOURCES`, check if `<WORKDIR>/bugs/bug-matches-<source>.json` exists
2. If **ALL** files exist:
   a. Read each file and build a lookup map: `error_signature` → `{duplicates, regressions}` (aggregate across all source files)
   b. For each per-source candidate across all sources, look up its `error_signature` in the map
   c. If **ALL** candidates have a match: display a notice and **skip Step 2**, proceed directly to Step 2a:

      ```text
      Using cached Jira search results from prior run.
      To force fresh Jira searches, delete the bug mapping files:
        rm <WORKDIR>/bugs/bug-matches-*.json
      ```

   d. If **ANY** candidate has no match in the cache: discard all cached data and proceed to Step 2 (full Jira search for all candidates — do not mix cached and fresh results)
3. If **ANY** source file is missing: proceed to Step 2 (full Jira search)

### Step 2: Search Jira for Existing Bugs and Write Bug Mapping Files

For each **per-source** bug candidate (iterate over each source's candidate list separately — do NOT merge first), run **ALL THREE** searches (A, B, C) described below. Do NOT skip any search — the HTML report depends on complete `duplicates` and `regressions` arrays. The `keywords` and `test_ids` fields are pre-computed by the script — use them directly.

**Search A — Keyword search (multiple focused queries)**:

1. Use the pre-computed `keywords` array from the candidate (already filtered for stop words and ranked by specificity)
2. Run **2-3 separate searches in parallel**, each using 1-2 keywords from the array. Do NOT put all keywords into a single `text ~` query — Jira requires all terms to match, so queries with 3+ keywords are fragile and miss issues that use slightly different wording.

   ```python
   # Example: candidate.keywords = ["invalidclienttokenid", "cloudformation", "createstack", "aws-2"]
   # Search A1: most distinctive keyword
   mcp__jira__jira_search(jql='... AND issuetype = Bug AND text ~ "invalidclienttokenid" ...', limit=5)
   # Search A2: second keyword
   mcp__jira__jira_search(jql='... AND issuetype = Bug AND text ~ "cloudformation" ...', limit=5)
   ```

3. Merge and deduplicate results from all A-series queries before proceeding

**Search B — Test case ID search (MANDATORY when `test_ids` is non-empty)**:
Use the pre-computed `test_ids` array from the candidate. For EACH ID, run TWO separate searches:

```text
# Search B1: bare number
jql: ... AND issuetype = Bug AND text ~ "68256" AND status not in (Closed, Verified) ...

# Search B2: OCP-prefixed form (OpenShift Polarion convention)
jql: ... AND issuetype = Bug AND text ~ "OCP-68256" AND status not in (Closed, Verified) ...
```

**Why both forms are required**: Jira's text indexer treats `OCP-68256` as a single token, so `text ~ "68256"` will NOT match issues containing `OCP-68256`, and vice versa. Skipping either form WILL cause missed duplicates.

**After searches A and B**:

1. Merge and deduplicate results from all search queries (A, B1, B2)

**Search C — Regression check (MANDATORY for every candidate)**:

This search is **required** for every candidate, even when Search A/B already found open duplicates. It populates the `regressions` array in the mapping file, which the HTML report renders separately from open bugs.

Run a keyword search against closed/verified issues:

```python
mcp__jira__jira_search(
  jql='((project = OCPBUGS AND component = MicroShift) OR project = USHIFT) AND issuetype = Bug AND text ~ "<keywords>" AND status in (Closed, Verified) ORDER BY updated DESC',
  limit=5
)
```

Record **every** result as a regression entry — these are shown in the HTML report with distinct "Regressions" styling.

**Note**: Run searches in parallel where possible. All three searches (A, B, C) can run concurrently per candidate. When using sub-agents for Jira searches, launch them as **foreground** agents in a **single message** (do NOT use `run_in_background`). Foreground agents in the same message run concurrently — this is just as fast as background agents but keeps your turn active until all complete and lets you collect results for the bug mapping files.

**Recording results — duplicates and regressions**:

After completing ALL searches for a candidate:

1. **`duplicates` array**: Must contain ALL unique open bugs returned by searches A and B (deduplicated by key). Do NOT stop at the first match — record every issue returned.
2. **`regressions` array**: Must contain ALL unique closed/verified bugs returned by Search C (deduplicated by key). An empty `regressions` array means Search C returned zero results — not that it was skipped.
3. Do NOT filter either array for relevance — downstream scripts use overlap similarity to match bugs to candidates; removing results breaks the matching pipeline.

**Writing bug mapping files**:

After all Jira searches are complete for a source, write `<WORKDIR>/bugs/bug-matches-<source>.json`:

```json
{
  "source": "<source>",
  "date": "YYYY-MM-DD",
  "candidates": [
    {
      "error_signature": "<error_signature>",
      "severity": "<N>",
      "failure_type": "<build|test|infrastructure>",
      "step_name": "<step_name>",
      "affected_jobs": "<count for this source>",
      "duplicates": [
        {"key": "<JIRA-KEY>", "summary": "<summary>", "status": "<status>", "assignee": "<display_name>", "updated": "<YYYY-MM-DD>"}
      ],
      "regressions": [
        {"key": "<JIRA-KEY>", "summary": "<summary>", "status": "<status>", "assignee": "<display_name>", "updated": "<YYYY-MM-DD>"}
      ]
    }
  ],
  "open_bugs": [
    {
      "key": "USHIFT-1234",
      "summary": "...",
      "status": "In Progress",
      "priority": "Normal",
      "assignee": "jdoe",
      "created": "2026-05-01",
      "updated": "2026-05-09"
    }
  ]
}
```

1. **IMPORTANT**: The `duplicates` and `regressions` arrays must contain ALL results from their respective searches — do NOT omit or filter (see "Recording results" above). Missing entries mean missing bug links in the HTML report.
2. Use empty arrays `[]` for `duplicates` and `regressions` only when the respective searches returned zero results.
3. The `failure_type` field must be set from the candidate's computed `failure_type` (via `classify_breakdown`). This field is required for downstream `--merge` to correctly skip infrastructure failures without needing `stack_layer`.

### Step 2a: Merge Candidates

Run the merge script (even for a single source — it produces a unified output with Jira data injected from the bug mapping files written in Step 2).

Before invoking, also check for any `bug-candidates-rebase-*.json` files in `<WORKDIR>/bugs`. If found, include them in the merge so rebase PR failures are deduplicated against release failures.

```text
python3 plugins/microshift-ci/scripts/search-bugs.py --merge <WORKDIR>/bugs/bug-candidates-<source1>.json [<source2>.json ...] [<WORKDIR>/bugs/bug-candidates-rebase-*.json] --output <WORKDIR>/bugs/bug-candidates-merged-<SOURCE_TAG>.json --workdir <WORKDIR>
```

This writes `<WORKDIR>/bugs/bug-candidates-merged-<SOURCE_TAG>.json`. Read and use this file for all subsequent steps.

### Step 3: Categorize Candidates and Write Results

**Goal**: Apply the decision policy to each merged candidate, categorizing it as `suggest` (draft a ticket suggestion), `linked` (matches an existing open bug), or `skip` (infrastructure or stale). Write the results JSON for the deterministic report generator.

**Actions**:

1. Read the merged candidates from `<WORKDIR>/bugs/bug-candidates-merged-<SOURCE_TAG>.json`
2. For each candidate, apply the **Decision Policy** (see below)
3. Build the results array and write it to `<WORKDIR>/bugs/bug-results-<SOURCE_TAG>.json`

#### Decision Policy

Apply these rules in order for each candidate:

| Condition | Action | Reason |
|-----------|--------|--------|
| `failure_type` is `"infrastructure"` | **skip** | `"Infrastructure failure — not a product bug"` |
| Has open duplicates from Jira search | **linked** | `"Linked to existing bug <JIRA-KEY>"` — use the first entry in the candidate's `duplicates` array |
| Has closed regressions but no open duplicates — and **all** job `finished` dates are **on or before** the regression's `updated` date | **skip** | `"Stale failure predating fix for <JIRA-KEY> (updated <YYYY-MM-DD>)"` |
| Has closed regressions but no open duplicates — and **any** job `finished` date is **after** the regression's `updated` date | **suggest** | `"Potential regression of <JIRA-KEY> — suggest filing a new bug"` |
| No duplicates, no regressions | **suggest** | `"No existing bugs found — suggest filing a new bug"` |

#### Results JSON

Write to `<WORKDIR>/bugs/bug-results-<SOURCE_TAG>.json`:

```json
{
  "mode": "search",
  "date": "YYYY-MM-DD",
  "results": [
    {
      "error_signature": "<matches candidate's error_signature exactly>",
      "action": "suggest",
      "jira_key": "",
      "skip_category": "",
      "reason": "No existing bugs found — suggest filing a new bug"
    },
    {
      "error_signature": "<matches candidate's error_signature exactly>",
      "action": "linked",
      "jira_key": "USHIFT-6938",
      "skip_category": "",
      "reason": "Linked to existing bug USHIFT-6938"
    },
    {
      "error_signature": "<matches candidate's error_signature exactly>",
      "action": "skip",
      "jira_key": "",
      "skip_category": "infrastructure",
      "reason": "Infrastructure failure — not a product bug"
    }
  ]
}
```

All fields are required on every entry:

- `error_signature`: must match the candidate's `error_signature` exactly
- `action`: one of `suggest`, `linked`, `skip`
- `jira_key`: the matched JIRA key for `linked`; empty string `""` for `suggest`/`skip`
- `skip_category`: one of `infrastructure`, `stale_regression` for `skip`; empty string `""` for other actions
- `reason`: human-readable explanation, always non-empty

There must be exactly one result entry per merged candidate. Do NOT skip any candidates.

### Step 4: Generate Report (Deterministic Script)

**Actions**:

1. Ensure `<WORKDIR>/bugs/bug-results-<SOURCE_TAG>.json` was written in Step 3
2. Generate the report:

   ```text
   python3 plugins/microshift-ci/scripts/search-bugs.py \
     --report <WORKDIR>/bugs/bug-results-<SOURCE_TAG>.json \
     --candidates <WORKDIR>/bugs/bug-candidates-merged-<SOURCE_TAG>.json \
     --workdir <WORKDIR>
   ```

3. Display the report output to the user

## Examples

### Example 1: Single Release

```bash
/microshift-ci:find-regressions 4.22
```

Searches Jira for existing bugs matching release 4.22 failures and drafts suggestions for unmatched ones.

### Example 2: PR Failures

```bash
/microshift-ci:find-regressions pr-6396
```

Searches for bugs matching PR #6396 failures.

### Example 3: Rebase PR

```bash
/microshift-ci:find-regressions rebase-release-4.22
```

Resolves the rebase PR for release 4.22 and searches for matching bugs.

### Example 4: Multi-Source

```bash
/microshift-ci:find-regressions main,4.22,4.21,4.20,5.0
```

Searches across 5 releases with cross-release dedup applied. Failures appearing in multiple releases are merged into single candidates.

### Example 5: No Job Files Found

```bash
/microshift-ci:find-regressions 4.19
```

```text
No job files found for 4.19 in <WORKDIR>
```

## Notes

- This command does NOT create or update JIRA issues — it only searches for existing bugs and drafts suggestions
- This command does NOT run CI analysis — it only consumes existing analysis files from `<WORKDIR>`
- Supports two file naming patterns:
  - Release jobs: `jobs/release-<release>-job-*.json` (from `/microshift-ci:doctor`)
  - PR jobs: `jobs/prs-job-*-pr<number>-*.json` (from `/microshift-ci:doctor`)
- Candidates categorized as `suggest` can be filed manually via the "Create Bug in JIRA" button in the HTML report, which prefills the Jira form with failure details
- Candidates are always merged via `search-bugs.py --merge` (even for a single source) to produce a unified output with Jira data injected. Cross-release deduplication uses fuzzy signature matching (token-based overlap similarity, 50% threshold)
- Infrastructure failures (`failure_type: "infrastructure"`) are automatically skipped — these are transient CI/cloud issues, not product bugs. Classification uses the same step-name-based logic as the HTML report (`classify_breakdown` in `classify.py`)
- Duplicate search covers both USHIFT and OCPBUGS projects
- Valid JSON format in job files is required — this is a contract with the `microshift-ci:prow-job-analyzer` agent
- Machine-readable bug mapping files (`bugs/bug-matches-<source>.json`) are written per source in Step 2. They serve two purposes: (1) consumed by `create-report.py` to show JIRA bug links and "Create Bug" buttons in the HTML report, and (2) consumed by `--merge` in Step 2a for Jira-based deduplication across releases

## Related Skills

- **microshift-ci:doctor**: Produces job analysis files consumed by this command
- **microshift-ci:prow-job**: Command that produces individual job reports as JSON
- **microshift-ci:close-stale-bugs**: Closes stale unlinked bugs (should run after this skill)
- **microshift-ci:doctor-refresh**: Regenerate the HTML report from existing data
