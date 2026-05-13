---
name: microshift-ci:create-bugs
argument-hint: <source1>[,<source2>,...] [--create] [--auto]
description: Create JIRA bugs from analyze-ci failure reports with cross-release deduplication (dry-run by default)
user-invocable: true
allowed-tools: Bash, Read, Write, Glob, Grep, Agent, mcp__jira__jira_search, mcp__jira__jira_create_issue, mcp__jira__jira_get_issue, mcp__jira__jira_get_transitions, mcp__jira__jira_transition_issue, mcp__jira__jira_add_comment
---

# microshift-ci:create-bugs

## Synopsis

```bash
/microshift-ci:create-bugs <source> [--create] [--auto]
/microshift-ci:create-bugs <source1>,<source2>,... [--create] [--auto]
```

## Description

Reads individual job analysis reports produced by `microshift-ci:doctor` and creates JIRA bugs in USHIFT for CI test failures. Operates in **dry-run mode by default** - it shows what bugs would be created without actually creating them. Use `--create` to perform actual issue creation.

Candidates are always **fuzzy-matched across sources** using token-based Jaccard similarity (50% threshold) with step-name bucketing — the same root cause appearing in multiple releases becomes a single candidate and a single Jira bug referencing all affected releases.

This command does NOT re-analyze CI jobs. It consumes existing job analysis files from `<WORKDIR>`.

## Arguments

- `<ARGUMENTS>` (required): Source identifier(s), optionally followed by flags
  - `<sources>` (required): One or more comma-separated sources. Each source is one of:
    - **Release version** (e.g., `4.22`, `main`): Looks for files matching `analyze-ci-release-<release>-job-*.txt`
    - **PR number** (e.g., `pr-6396` or `pr6396`): Looks for files matching `analyze-ci-prs-job-*-pr<number>-*.txt`
    - **Rebase PR shorthand** (e.g., `rebase-release-4.22`): Resolves to the corresponding rebase PR by scanning existing `analyze-ci-prs-job-*` files for the matching release version in their content
  - `--create` (optional): Actually create JIRA issues. Without this flag, only a dry-run report is produced.
  - `--auto` (optional): Replace interactive per-candidate prompts with an auto-decision policy. Without `--create`, labels each candidate with what would happen (dry-run). With `--create`, executes the auto-decisions without prompting. See Step 3 for the auto-decision policy.

## Prerequisites

- Job analysis files must already exist in `<WORKDIR>`:
  - For releases: `analyze-ci-release-<release>-job-*.txt` (produced by `/microshift-ci:doctor`)
  - For PRs: `analyze-ci-prs-job-*-pr<number>-*.txt` (produced by `/microshift-ci:doctor`)
- Each job file must contain a `--- STRUCTURED SUMMARY ---` block (see below)
- MCP Jira server must be configured and accessible
- User must have permissions to create issues in USHIFT

### STRUCTURED SUMMARY Block

Each job analysis file produced by `/microshift-ci:prow-job` must end with a machine-readable block:

```text
--- STRUCTURED SUMMARY ---
SEVERITY: <1-5>
STACK_LAYER: <AWS Infra|External Infrastructure|build phase|deploy phase|test setup phase|Test Configuration|test|teardown>
STEP_NAME: <the CI step where the error occurred>
ERROR_SIGNATURE: <concise, unique description of the root cause error>
ROOT_CAUSE: <one-line description of WHY the failure happened — the underlying mechanism, not the surface symptom>
RAW_ERROR: <verbatim primary error message from logs — used for deterministic grouping>
INFRASTRUCTURE_FAILURE: <true|false>
JOB_URL: <full prow job URL>
JOB_NAME: <full periodic job name>
RELEASE: <X.YY>
FINISHED: <job finish date in YYYY-MM-DD format>
--- END STRUCTURED SUMMARY ---
```

If a job file lacks this block, it is skipped with a warning.

## Work Directory

Compute once at the start by running `date +%y%m%d` and substituting into the path below. In all commands, replace `<WORKDIR>` with the computed path — do not use shell variables.

```text
/tmp/microshift-ci-claude-workdir.<YYMMDD>
```

## Implementation Steps

### Step 1: Prepare Bug Candidates (Deterministic Script)

**Actions**:

1. Parse `<ARGUMENTS>` to extract source(s) and detect `--create` and `--auto` flags
2. Split sources on commas to get `SOURCES` list (e.g., `["4.22"]` or `["4.20", "4.21", "4.22", "5.0", "main"]`)
3. Determine mode: if `--create` is present, set `MODE=create`; otherwise `MODE=dry-run`
4. Determine today's WORKDIR path by running `date +%y%m%d` and substituting into `/tmp/microshift-ci-claude-workdir.YYMMDD`. Run `mkdir -p` on it.
5. For **each source** in `SOURCES`, run the preparation script:

   ```text
   python3 plugins/microshift-ci/scripts/search-bugs.py <source> --workdir <WORKDIR>
   ```

   Each invocation writes `<WORKDIR>/analyze-ci-bug-candidates-<source>.json` containing parsed and deduplicated bug candidates with pre-computed `keywords`, `test_ids`, `jobs[]`, and `analysis_text`.

**Error Handling**:

- No arguments: show usage and stop
- Script exits with error if no job files found — relay its error message to the user

### Step 1a: Check for Cached Jira Results

After loading per-source candidates (Step 1), check whether bug mapping files already exist for ALL sources. These files are written by Step 2 on every run and contain the Jira search results (`duplicates[]`, `regressions[]`). If they exist and cover all per-source candidates, Step 2 can be skipped entirely.

**Actions**:

1. For each source in `SOURCES`, check if `<WORKDIR>/analyze-ci-bugs-<source>.json` exists
2. If **ALL** files exist:
   a. Read each file and build a lookup map: `error_signature` → `{duplicates, regressions}` (aggregate across all source files)
   b. For each per-source candidate across all sources, look up its `error_signature` in the map
   c. If **ALL** candidates have a match: display a notice and **skip Step 2**, proceed directly to Step 2a:

      ```text
      Using cached Jira search results from prior run.
      To force fresh Jira searches, delete the bug mapping files:
        rm <WORKDIR>/analyze-ci-bugs-*.json
      ```

   d. If **ANY** candidate has no match in the cache: discard all cached data and proceed to Step 2 (full Jira search for all candidates — do not mix cached and fresh results)
3. If **ANY** source file is missing: proceed to Step 2 (full Jira search)

### Step 2: Search Jira for Existing Bugs and Write Bug Mapping Files

For each **per-source** bug candidate (iterate over each source's candidate list separately — do NOT merge first), run ALL of the following searches. The `keywords` and `test_ids` fields are pre-computed by the script — use them directly.

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

**After all searches**:

1. Merge and deduplicate results from all search queries (A, B1, B2)
2. If potential duplicates are found, fetch their details with `mcp__jira__jira_get_issue` to show summary and status

**Search C — Regression check (closed/verified issues)**:
After completing searches A and B, run an additional keyword search against closed/verified issues to detect potential regressions:

```python
mcp__jira__jira_search(
  jql='((project = OCPBUGS AND component = MicroShift) OR project = USHIFT) AND issuetype = Bug AND text ~ "<keywords>" AND status in (Closed, Verified) ORDER BY updated DESC',
  limit=5
)
```

If results are found, fetch their details with `mcp__jira__jira_get_issue` and flag them as **"Potential regression of closed bug"** — distinct from open duplicates. These should be shown to the user but do NOT block creation; they serve as a warning that a previously fixed issue may have resurfaced.

**Note**: Run searches in parallel where possible.

**Query for open AI-generated bugs**: After completing all per-candidate searches, run one additional query to fetch all open bugs with the `microshift-ci-ai-generated` label:

```text
mcp__jira__jira_search(
  jql="project = USHIFT AND issuetype = Bug AND labels = microshift-ci-ai-generated AND status not in (Closed, Verified) ORDER BY updated DESC",
  fields="summary,status,priority,assignee,created,updated",
  limit=50
)
```

If more than 50 results, paginate with `start_at` until all issues are fetched. For each issue, extract: `key`, `summary`, status name, priority name, assignee display name, `created` and `updated` truncated to date only (first 10 characters).

**After completing all Jira searches**, write machine-readable bug mapping files per source. For each source in `SOURCES`, write `<WORKDIR>/analyze-ci-bugs-<source>.json` using this JSON format:

```json
{
  "source": "<source>",
  "date": "YYYY-MM-DD",
  "candidates": [
    {
      "error_signature": "<error_signature>",
      "severity": <N>,
      "failure_type": "<build|test|infrastructure>",
      "step_name": "<step_name>",
      "affected_jobs": <count for this source>,
      "duplicates": [
        {"key": "<JIRA-KEY>", "summary": "<summary>", "status": "<status>", "updated": "<YYYY-MM-DD>"}
      ],
      "regressions": [
        {"key": "<JIRA-KEY>", "summary": "<summary>", "status": "<status>", "updated": "<YYYY-MM-DD>"}
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

1. **IMPORTANT**: These files must be written in BOTH dry-run and create modes. They enable `create-report.py` to show linked bugs in the HTML report, and are consumed by the merge step (Step 2a) for Jira-based deduplication.
2. Use empty arrays `[]` for `duplicates` and `regressions` when none are found.
3. The `failure_type` field must be set from the candidate's computed `failure_type` (via `classify_breakdown`). This field is required for downstream `--merge` to correctly skip infrastructure failures without needing `stack_layer`.

### Step 2a: Merge Candidates

Run the merge script (even for a single source — it produces a unified output with Jira data injected from the bug mapping files written in Step 2).

Before invoking, also check for any `analyze-ci-bug-candidates-rebase-*.json` files in `<WORKDIR>`. If found, include them in the merge so rebase PR failures are deduplicated against release failures.

```text
python3 plugins/microshift-ci/scripts/search-bugs.py --merge <WORKDIR>/analyze-ci-bug-candidates-<source1>.json [<source2>.json ...] [<WORKDIR>/analyze-ci-bug-candidates-rebase-*.json] --workdir <WORKDIR>
```

This writes `<WORKDIR>/analyze-ci-bug-candidates-merged.json`. Read and use this file for all subsequent steps.

### Step 3: Present Bug Candidates to User

**Actions**:

1. Display a numbered list of all bug candidates with:
   - Summary (derived from error signature)
   - Severity and affected job count
   - Step name(s) where failure occurred
   - Releases: list of releases with job counts per release
   - List of affected job URLs
   - Potential duplicate JIRAs found (if any), with key, summary, and status
   - Mode indicator: `[WOULD CREATE]` / `[WOULD SKIP]` or `[WILL CREATE]`

2. **In dry-run mode** (`--create` NOT specified, `--auto` NOT specified):
   - Apply the Auto-Decision Policy (see below) to label each candidate `[WOULD CREATE]` or `[WOULD SKIP]` — do NOT prompt the user
   - Include `Decision:` line per candidate (same format as Step 5 report)
   - After listing all candidates, show a summary:

     ```text
     SUMMARY
       Sources processed: N
       Unique failures: N (from M total candidates)
       Would create: N
       Would skip (Jira duplicate): N
       Would skip (infrastructure): N
       Would skip (stale regression): N

     To create these bugs, run:
       /microshift-ci:create-bugs <sources> --create
       /microshift-ci:create-bugs <sources> --auto --create
     ```

   - Do NOT prompt for any actions. Do NOT create any issues. Do NOT proceed to Steps 4/4a (create/reopen). Continue to Step 5 (report).

3. **In create mode** (`--create` specified, `--auto` NOT specified):
   - For each candidate, prompt the user:

     ```text
     Bug Candidate N/M:
       Summary: "<derived summary>"
       Severity: X (affects Y jobs total)
       Step: <step_name>
       Releases: <source1> (N jobs), <source2> (N jobs)
       Grouped with:                          ← only when merged_signatures is non-empty
         - <other_error_signature_1>
         - <other_error_signature_2>
       Jobs:
         - <job_url_1>
         - <job_url_2>
       Potential Duplicates (open):
         - USHIFT-XXXXX: "<summary>" [Status] (or OCPBUGS-YYYYY)
         (or "None found")
       Potential Regressions (closed):
         - USHIFT-YYYYY (or OCPBUGS-YYYYY): "<summary>" [Status] potential regression
         (or "None found")
     ```

   - Select the prompt template based on whether closed regressions were found:

     ```text
     # ACTION_PROMPT_WITH_REOPEN (use when closed regressions exist):
     Action? [c]reate / [s]kip / [l]ink-to-existing <JIRA-KEY> / [r]eopen <JIRA-KEY>:

     # ACTION_PROMPT_NO_REOPEN (use when no closed regressions exist):
     Action? [c]reate / [s]kip / [l]ink-to-existing <JIRA-KEY>:
     ```

   - **create**: Proceed to Step 4
   - **skip**: Skip this candidate, move to next
   - **link-to-existing**: Validate the key by calling `mcp__jira__jira_get_issue(issue_key=<JIRA-KEY>)`. If the issue exists, record the key and move to next. If the call fails or returns not-found, show an error (e.g., `"JIRA key <JIRA-KEY> not found — check for typos"`) and re-prompt with the same `Action?` choices.
   - **reopen**: Validate the provided JIRA key before proceeding. Call `mcp__jira__jira_get_issue(issue_key=<JIRA-KEY>)` to confirm the issue exists, then verify that the key matches one of the candidate's closed regressions found in Search C, that the issue status is Closed or Verified, and that the issue type is Bug. If validation fails (key not found, not in the candidate's closed regression list, not in Closed/Verified state, or not a Bug), show an error (e.g., `"JIRA key <JIRA-KEY> not eligible for reopen — must be a Bug closed regression"`) and re-prompt with the same `Action?` choices. If validation passes, proceed to Step 4a.

4. **In auto dry-run mode** (`--auto` without `--create`):
   - Same as dry-run mode (Step 3.2) — auto-decision policy is always applied
   - Continue to Step 5

5. **In auto create mode** (`--auto --create`):
   - Apply the auto-decision policy and execute actions — do NOT prompt the user
   - For candidates where decision is "create": proceed to Step 4
   - For candidates where decision is "skip": record the skip reason and move to next
   - Continue to Step 5

#### Auto-Decision Policy

When `--auto` is active, apply these rules in order for each candidate:

| Condition | Decision | Reason |
|-----------|----------|--------|
| `failure_type` is `"infrastructure"` | **Skip** | `"Infrastructure failure — not a product bug"` |
| Has open duplicates from Jira search | **Skip** | `"Duplicate of <JIRA-KEY>"` |
| Has closed regressions but no open duplicates — and **all** job `finished` dates are **on or before** the regression's `updated` date | **Skip** | `"Stale failure predating fix for <JIRA-KEY> (updated <YYYY-MM-DD>)"` |
| Has closed regressions but no open duplicates — and **any** job `finished` date is **after** the regression's `updated` date | **Create** | Add `"Potential regression of <JIRA-KEY>"` to the bug description's Additional Info section |
| No duplicates, no regressions | **Create** | `"No existing duplicates"` |

### Step 4: Create Bug via MCP (create mode only)

**Actions**:
For each candidate where user chose "create":

1. **Construct the bug summary**:
   - Format: `"MicroShift CI: <error_signature>"` (truncate to 100 chars if needed)

2. **Construct the bug description** using **Markdown** format (the MCP Jira tool accepts Markdown and automatically converts it to Jira wiki markup — do NOT write Jira wiki markup directly):

   `````text
   ## Description of problem

   CI job failures detected across MicroShift releases: <release1>, <release2>, ...

   <concise description derived from the error signature and analysis text>

   ## Version-Release number of selected component (if applicable)

   <release1>, <release2>, ...

   ## How reproducible

   Always (fails consistently in CI across multiple releases)

   ## Steps to Reproduce

   1. Run the CI job(s) listed below
   2. Observe failure in step: <step_name>

   ## Actual results

   ````

   <error details extracted from the analysis text — the specific error message and relevant log context>

   ````

   ## Expected results

   CI job should pass successfully.

   ## Additional info

   **Stack Layer:** <stack_layer>
   **CI Step:** <step_name>
   **Error Severity:** <severity>/5
   **Number of affected jobs:** <total count across all releases>
   **Last observed:** <latest finished date across all jobs, YYYY-MM-DD>
   **Affected Releases:** <release1> (<N> jobs), <release2> (<N> jobs)

   **Affected Jobs:**
   <for each release>
   *<release>:*
   <for each job in release>
   - [<job_name>](<job_url>)
   </for each>
   </for each>

   **Source:** Auto-generated by /microshift-ci:create-bugs from CI analysis output.
   `````

3. **Create the issue**:

   ```python
   mcp__jira__jira_create_issue(
       project_key="USHIFT",
       summary="MicroShift CI: <error_signature>",
       issue_type="Bug",
       description="<constructed description>",
       components="MicroShift",
       additional_fields={
           "labels": ["microshift-ci-ai-generated"],
           "security": {"name": "Red Hat Employee"}
       }
   )
   ```

4. **Record the result**: Store the created issue key for the final report.

**Error Handling**:

- **Interactive mode** (no `--auto`): If MCP call fails, report error, ask user if they want to retry or skip. Do NOT retry automatically.
- **Auto mode** (`--auto`): If MCP call fails, log the error, record the candidate as "FAILED" in the report, and continue to the next candidate. Do NOT prompt or retry.

### Step 4a: Reopen Closed Bug as Regression (create mode only)

**Precondition**: The JIRA issue must be a Bug in Closed or Verified state (validated in Step 3). If the issue type is not Bug, do not proceed — show an error and re-prompt.

**Actions**:
For each candidate where user chose "reopen":

1. **Get available transitions** for the closed issue:

   ```python
   mcp__jira__jira_get_transitions(issue_key="<JIRA-KEY>")
   ```

2. **Find the reopen transition**: Look for a transition whose name is exactly "To Do", "New", or "Backlog" (case-insensitive). If no suitable transition is found, report the error and ask the user whether to create a new bug instead or skip.

3. **Construct a regression comment** describing the new occurrences:

   ```text
   ## Regression: issue has resurfaced

   This issue was previously closed but the same failure has been detected again in CI.

   **Error Signature:** <error_signature>
   **Error Severity:** <severity>/5
   **Number of affected jobs:** <count>
   **Last observed:** <finished date>

   **Affected Jobs:**
   - [<job_name>](<job_url>)
   ...

   Reopened automatically by /microshift-ci:create-bugs.
   ```

4. **Transition the issue** to reopen it:

   ```python
   mcp__jira__jira_transition_issue(
       issue_key="<JIRA-KEY>",
       transition_id="<reopen_transition_id>",
       comment="<regression comment>"
   )
   ```

5. If the transition call does not support inline comments, add the comment separately:

   ```python
   mcp__jira__jira_add_comment(
       issue_key="<JIRA-KEY>",
       body="<regression comment>"
   )
   ```

6. **Record the result**: Store the reopened issue key for the final report.

**Error Handling**:

- If no reopen-like transition is available, report available transitions to user and ask whether to create a new bug or skip
- If the transition fails, report error and ask user if they want to retry, create a new bug instead, or skip
- Do NOT retry automatically

### Step 5: Generate Results Report

The report file must use the exact format below. Both the on-screen display (Step 3) and the saved report file follow the same template.

**Actions**:

1. Save report to `<WORKDIR>/analyze-ci-create-bugs-<source>.txt` for a single source, or `<WORKDIR>/analyze-ci-create-bugs-merged.txt` for multiple sources (overwrite if exists)
2. Display summary to user:

**Dry-run report format**:

```text
═══════════════════════════════════════════════════════════════
ANALYZE-CI CREATE BUGS - DRY-RUN REPORT
Sources: <source1>, <source2>, ...
Date: YYYY-MM-DD
═══════════════════════════════════════════════════════════════

CANDIDATES (<N> unique failures from <M> total across <S> sources)

  1. [WOULD CREATE]
     MicroShift CI: <error_signature>
     Severity: X | Total Jobs: Y | Step: <step_name>
     Releases: <source1> (N jobs), <source2> (N jobs)
     Grouped with:                          ← only when merged_signatures is non-empty
       - <other_error_signature_1>
       - <other_error_signature_2>
     Potential Duplicates: None
     Potential Regressions: None
     Decision: No existing duplicates

  2. [WOULD SKIP]
     MicroShift CI: <error_signature>
     Severity: X | Total Jobs: Y | Step: <step_name>
     Releases: <source1> (N jobs)
     Potential Duplicates: USHIFT-XXXXX [Status]
     Decision: Duplicate of USHIFT-XXXXX

  3. [WOULD CREATE]
     MicroShift CI: <error_signature>
     Severity: X | Total Jobs: Y | Step: <step_name>
     Releases: <source1> (N jobs), <source2> (N jobs), <source3> (N jobs)
     Grouped with:                          ← only when merged_signatures is non-empty
       - <other_error_signature_1>
     Potential Regressions: USHIFT-YYYYY [Closed]
     Decision: Potential regression of USHIFT-YYYYY [Closed]

  4. [WOULD SKIP]
     MicroShift CI: <error_signature>
     Severity: X | Total Jobs: Y | Step: <step_name>
     Releases: <source1> (N jobs)
     Potential Regressions: USHIFT-ZZZZZ [Closed]
     Decision: Stale failure predating fix for USHIFT-ZZZZZ (updated YYYY-MM-DD)

...

SUMMARY
  Sources processed: N
  Unique failures: N (from M total candidates)
  Would create: N
  Would skip (Jira duplicate): N
  Would skip (stale regression): N

To create these bugs, run:
  /microshift-ci:create-bugs <source1>,<source2>,... --create
  /microshift-ci:create-bugs <source1>,<source2>,... --auto --create

Report saved: <WORKDIR>/analyze-ci-create-bugs-<source|merged>.txt
═══════════════════════════════════════════════════════════════
```

**Create mode report format**:

```text
═══════════════════════════════════════════════════════════════
ANALYZE-CI CREATE BUGS - CREATION REPORT
Sources: <source1>, <source2>, ...
Date: YYYY-MM-DD
═══════════════════════════════════════════════════════════════

RESULTS (<N> unique failures from <M> total across <S> sources)

  1. USHIFT-12345 (CREATED)
     MicroShift CI: <error_signature>
     Releases: <source1> (N jobs), <source2> (N jobs)
     Grouped with:                          ← only when merged_signatures is non-empty
       - <other_error_signature_1>
     URL: https://redhat.atlassian.net/browse/USHIFT-12345

  2. SKIPPED
     MicroShift CI: <error_signature>
     Releases: <source1> (N jobs)
     Reason: Duplicate of USHIFT-99999

  3. USHIFT-12346 (CREATED)
     MicroShift CI: <error_signature>
     Releases: <source1> (N jobs), <source3> (N jobs)
     URL: https://redhat.atlassian.net/browse/USHIFT-12346
     Reason: Potential regression of USHIFT-88888 [Closed]

...

SUMMARY
  Sources processed: N
  Unique failures: N (from M total candidates)
  Created: N
  Skipped: N
  Linked to existing: N
  Reopened: N
  Failed: N

Report saved: <WORKDIR>/analyze-ci-create-bugs-<source|merged>.txt
═══════════════════════════════════════════════════════════════
```

## Examples

### Example 1: Dry-Run for a Release (Default)

```bash
/microshift-ci:create-bugs 4.22
```

Shows what bugs would be created from release 4.22 analysis without creating anything.

### Example 2: Create Bugs for a Release

```bash
/microshift-ci:create-bugs 4.22 --create
```

Interactively creates bugs from release 4.22 analysis.

### Example 3: Dry-Run for a PR

```bash
/microshift-ci:create-bugs pr-6396
```

Shows what bugs would be created from PR #6396 analysis.

### Example 4: Create Bugs for a Rebase PR

```bash
/microshift-ci:create-bugs rebase-release-4.22 --create
```

Resolves the rebase PR for release 4.22, then interactively creates bugs.

### Example 5: Multi-Source Dry-Run

```bash
/microshift-ci:create-bugs main,4.22,4.21,4.20,5.0
```

Shows all failures across 5 releases with cross-release dedup applied. Failures appearing in multiple releases are merged into single candidates with `Releases:` lines.

### Example 6: Multi-Source Auto Create

```bash
/microshift-ci:create-bugs main,4.22,4.21,4.20,5.0 --auto --create
```

Automatically creates bugs across all releases, skipping Jira duplicates. Cross-release duplicates are merged into single bugs referencing all affected releases.

### Example 7: Auto Dry-Run

```bash
/microshift-ci:create-bugs 4.22 --auto
```

Shows what auto-decisions would be made (create/skip) without actually creating anything.

### Example 8: Multi-Source Interactive Create

```bash
/microshift-ci:create-bugs main,4.22 --create
```

Cross-release dedup is applied, then each merged candidate is presented for interactive action (create/skip/link/reopen).

### Example 9: No Job Files Found

```bash
/microshift-ci:create-bugs 4.19
```

```text
Error: No job analysis files found at <WORKDIR>/analyze-ci-release-4.19-job-*.txt

Run the analysis first:
  /microshift-ci:doctor 4.19
```

## Notes

- This command does NOT run CI analysis — it only consumes existing analysis files from `<WORKDIR>`
- Supports two file naming patterns:
  - Release jobs: `analyze-ci-release-<release>-job-*.txt` (from `/microshift-ci:doctor`)
  - PR jobs: `analyze-ci-prs-job-*-pr<number>-*.txt` (from `/microshift-ci:doctor`)
- Dry-run is the default to prevent accidental bug creation
- The `--create` flag triggers interactive mode where each candidate requires user confirmation
- The `--auto` flag replaces interactive prompts with deterministic auto-decisions (skip if duplicates exist, create otherwise)
- Combine `--auto --create` for fully unattended bug creation; `--auto` alone is a labeled dry-run
- Candidates are always merged via `search-bugs.py --merge` (even for a single source) to produce a unified output with Jira data injected. Cross-release deduplication uses fuzzy signature matching (token-based Jaccard similarity, 50% threshold)
- In `--auto` mode, infrastructure failures (`failure_type: "infrastructure"`) are automatically skipped — these are transient CI/cloud issues, not product bugs. Classification uses the same step-name-based logic as the HTML report (`classify_breakdown` in `classify.py`). In interactive mode (no `--auto`), all failures are shown and the user decides
- Bugs are created in USHIFT with component "MicroShift"; duplicate search covers both USHIFT and OCPBUGS
- All created bugs are labeled with `microshift-ci-ai-generated` for tracking
- Security level is set to "Red Hat Employee" on all created issues
- The STRUCTURED SUMMARY block in job files is required — this is a contract with `/microshift-ci:prow-job`
- Machine-readable bug mapping files (`analyze-ci-bugs-<source>.json`) are written per source in Step 2 (both dry-run and create modes). They serve two purposes: (1) consumed by `create-report.py` to show JIRA bug links in the HTML report, and (2) consumed by `--merge` in Step 2a for Jira-based deduplication across releases

## Related Skills

- **microshift-ci:doctor**: Produces job analysis files consumed by this command
- **microshift-ci:prow-job**: Command that produces individual job reports with STRUCTURED SUMMARY
- **jira:create-bug**: Interactive bug creation (not used here — we call MCP directly)
