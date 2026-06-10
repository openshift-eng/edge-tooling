---
name: microshift-ci:close-stale-bugs
argument-hint: "[--close]"
description: Close stale, unlinked, unassigned AI-generated bugs that no longer match current CI failures (dry-run by default)
user-invocable: true
allowed-tools: Bash, Read, Write, Glob, Grep, mcp__jira__jira_get_issue, mcp__jira__jira_get_transitions, mcp__jira__jira_transition_issue, mcp__jira__jira_update_issue
---

# microshift-ci:close-stale-bugs

## Synopsis

```bash
/microshift-ci:close-stale-bugs
/microshift-ci:close-stale-bugs --close
```

## Description

Identifies and closes stale AI-generated JIRA bugs that are no longer relevant. The CI Doctor workflow creates bugs for CI failures, but some become stale when the underlying failures resolve themselves. This skill cleans up those orphaned bugs.

A bug is closed when **all** of the following are true:

- **AI-generated**: Has label `microshift-ci-ai-generated` (guaranteed by the data source)
- **Unassigned**: No assignee has been set
- **Not linked to failures**: The bug does not match any current CI failure signatures
- **Stale**: The bug has not been updated for more than 10 days

Operates in **dry-run mode by default** — shows which bugs would be closed without taking action. Use `--close` to actually close them.

**Intended run order**: doctor → create-bugs → close-stale-bugs → refresh

## Arguments

- `<ARGUMENTS>` (optional): Flags only
  - `--close` (optional): Actually close matching JIRA issues. Without this flag, only a dry-run report is produced.

## Prerequisites

- An existing workdir from a prior `/microshift-ci:doctor` run (today's date)
- `bugs/bug-matches-summary.json` must exist in the workdir (produced by the doctor finalize step)
- MCP Jira server must be configured and accessible (for `--close` mode)

## Work Directory

Compute once at the start by running `date +%y%m%d` and substituting into the path below. In all commands, replace `<WORKDIR>` with the computed path.

```text
/tmp/microshift-ci-claude-workdir.<YYMMDD>
```

## Implementation Steps

### Step 1: Load Data

1. Parse `<ARGUMENTS>` for the `--close` flag. If present, set MODE to `close`; otherwise MODE is `dry-run`.
2. Compute today's `<WORKDIR>` by running `date +%y%m%d` and substituting into `/tmp/microshift-ci-claude-workdir.<YYMMDD>`.
3. Read `<WORKDIR>/bugs/bug-matches-summary.json`. If the file does not exist, report a **fatal error** and stop:

   ```text
   Error: bugs/bug-matches-summary.json not found in <WORKDIR>
   Run the full doctor workflow first: /microshift-ci:doctor <releases>
   ```

4. Parse the JSON. Check the `jira_query_available` field. If it is `false`, report a **warning** and stop:

   ```text
   Warning: JIRA bug data is unavailable (jira_query_available: false)
   The doctor run could not query JIRA for open bugs. Skipping stale bug cleanup.
   ```

5. Extract the `linked` and `unlinked` arrays. Each entry has: `key`, `summary`, `status`, `assignee`, `updated`.

### Step 2: Filter for Closure Candidates

Iterate over the `unlinked[]` array. These bugs are already AI-generated and not linked to current CI failure signatures. For each bug, evaluate the remaining two criteria:

1. **Unassigned**: The `assignee` field must be empty (empty string, null, or `"Unassigned"`). If the bug has an assignee, skip it with reason: `"Assigned to <assignee>"`.

2. **Stale**: Parse the `updated` field as a date. Compute the number of days since update: `days_since_update = today - updated_date`. If `days_since_update <= 10`, skip with reason: `"Updated <N> days ago (threshold: 10)"`.

Bugs that pass both checks are **closure candidates**.

Categorize each unlinked bug as either a closure candidate or skipped (with reason).

### Step 3: Report

Display a summary:

```text
STALE BUG REPORT (MODE: dry-run|close)
  Total open AI-generated bugs: <linked + unlinked count>
  Linked to current failures: <linked count>
  Unlinked: <unlinked count>
    Candidates for closure: <N>
    Skipped (assigned): <N>
    Skipped (recently updated): <N>

  Bugs to close:
    1. USHIFT-XXXX: <summary> (last updated <N> days ago)
    2. ...
```

If MODE is `dry-run`, write a prose summary to `<WORKDIR>/report-close-stale-bugs.txt` containing the report above followed by a per-bug breakdown listing each unlinked bug, its action (close or skip), and the reason. Then stop here.

### Step 4: Close Bugs (close mode only)

For each closure candidate:

1. **Discover the Close transition**: Call `mcp__jira__jira_get_transitions(issue_key="<KEY>")`. Find the transition whose name contains "Close" (case-insensitive). Record its `id`.

   If no Close transition is available from the current status, record the bug as `"failed"` with reason `"No Close transition available from status '<status>'"` and skip to the next bug.

   Cache the transition ID by status name — if multiple bugs share the same status, only query transitions once.

2. **Transition to Closed**: Call:

   ```python
   mcp__jira__jira_transition_issue(
       issue_key="<KEY>",
       transition_id="<close_transition_id>",
       fields='{"resolution": {"name": "Obsolete"}}',
       comment="Automatically closed: unassigned, not linked to current CI failures, and inactive for more than 10 days."
   )
   ```

3. **Add the tracking label**: Use `mcp__jira__jira_get_issue` to fetch the current labels, append `"microshift-ci-ai-closed"`, and call:

   ```python
   mcp__jira__jira_update_issue(
       issue_key="<KEY>",
       fields='{"labels": [<all_existing_labels>, "microshift-ci-ai-closed"]}'
   )
   ```

4. **Record the result**: Mark the bug as `"closed"` or `"failed"` in the results.

**Error Handling**:

- If the transition fails because resolution "Obsolete" does not exist, report a **fatal error** and stop — do not attempt other resolutions or continue to the next bug. The Jira project configuration must be fixed first.
- For all other MCP call failures, log the error, record the bug as `"failed"`, and continue to the next bug. Do NOT prompt or retry.

Display a final summary:

```text
RESULTS
  1. USHIFT-XXXX: CLOSED
  2. USHIFT-YYYY: CLOSED
  3. USHIFT-ZZZZ: FAILED — No Close transition available from status "In Review"

SUMMARY
  Processed: <N> | Closed: <N> | Failed: <N>
```

Write `<WORKDIR>/report-close-stale-bugs.txt` with the full report: the summary from Step 3, the per-bug breakdown, and the final action outcomes above.

### Step 5: Write Closed-Bugs JSON (close mode only)

If any bugs were actually closed successfully in Step 4, write `<WORKDIR>/close-stale-bugs/closed-bugs.json`:

```json
{"closed": ["USHIFT-1234", "USHIFT-5678"]}
```

Include only keys that were closed successfully (not failed). Do not write this file in dry-run mode or if no bugs were closed.

This file is consumed by `/microshift-ci:doctor-refresh` to exclude closed bugs from the HTML report.

## Examples

### Example 1: Dry-Run (Default)

```bash
/microshift-ci:close-stale-bugs
```

Shows which stale bugs would be closed without taking any action.

### Example 2: Close Stale Bugs

```bash
/microshift-ci:close-stale-bugs --close
```

Actually closes all matching bugs in JIRA.

## Related Skills

- **microshift-ci:doctor**: Full CI analysis workflow (produces the bugs summary file consumed by this skill)
- **microshift-ci:create-bugs**: Bug correlation and creation (should run before this skill)
- **microshift-ci:doctor-refresh**: Regenerate the HTML report (should run after this skill to reflect closures)

## Notes

- This skill does NOT re-analyze jobs or re-query JIRA for bug lists — it reads the pre-computed `bugs/bug-matches-summary.json` from the doctor finalize step
- The `unlinked[]` array in the summary file contains bugs that are open, AI-generated, and not matched to any current CI failure signature
- Bugs with an assignee are never closed — someone has picked up the work
- The 10-day staleness threshold ensures recently-created or recently-commented bugs are not prematurely closed
- The `microshift-ci-ai-closed` label enables tracking of auto-closed bugs separately from manually closed ones
