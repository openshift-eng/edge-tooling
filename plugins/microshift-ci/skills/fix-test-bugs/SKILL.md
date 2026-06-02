---
name: microshift-ci:fix-test-bugs
argument-hint: [--open | <USHIFT-1234>[,<USHIFT-5678>,...]] [--fix]
description: Attempt to fix CI bugs by opening PRs in openshift/microshift (dry-run by default)
user-invocable: true
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Agent, mcp__jira__jira_get_issue, mcp__jira__jira_batch_get_changelogs, mcp__jira__jira_search
---

# microshift-ci:fix-test-bugs

## Synopsis

```bash
/microshift-ci:fix-test-bugs --open
/microshift-ci:fix-test-bugs --open --fix
/microshift-ci:fix-test-bugs USHIFT-1234,USHIFT-5678
/microshift-ci:fix-test-bugs USHIFT-1234 --fix
/microshift-ci:fix-test-bugs USHIFT-1234,USHIFT-5678 --fix
```

## Description

Given a list of JIRA bug keys (or `--open` to auto-discover them), fetches each bug's details, evaluates eligibility, and attempts automated fixes in `test/`, `scripts/`, or `docs/` of openshift/microshift, opening PRs against `main` for human review. Changes to product code (`cmd/`, `pkg/`, `vendor/`, etc.) are never attempted.

Operates in **dry-run mode by default** — shows which bugs are eligible and what fixes would be attempted. Use `--fix` to perform actual fixes.

## Arguments

- `<ARGUMENTS>` (required): Must include either `--open` or explicit bug keys, optionally followed by flags
  - `--open` (required if no keys given): Query JIRA for all unresolved AI-generated bugs (`labels = microshift-ci-ai-generated AND resolution = Unresolved`) and use the resulting keys. Mutually exclusive with explicit keys.
  - `<keys>` (required if no `--open`): One or more USHIFT bug keys (e.g., `USHIFT-1234` or `USHIFT-1234,USHIFT-5678`). Mutually exclusive with `--open`.
  - `--fix` (optional): Attempt fixes. Without this flag, only a dry-run report is produced.

## Work Directory

Compute once at the start by running `date +%y%m%d` and substituting into the path below. In all commands, replace `<WORKDIR>` with the computed path — do not use shell variables.

```text
/tmp/microshift-ci-claude-workdir.<YYMMDD>
```

Create `<WORKDIR>/fix-test-bugs/` at the start of the skill for intermediate files. The repo clone lives at `<WORKDIR>/microshift/` (shared with other skills).

## Prerequisites

- `gh` CLI authenticated with PR creation permissions
- Git user must have a fork of openshift/microshift (for pushing branches)
- MCP Jira server must be configured (for fetching bug details)

## Eligibility Decision Tree

Evaluated in order per bug. Must pass all gates to be eligible.

**Allowed directories** for fixes: `test/`, `scripts/`, `docs/`

| Gate | Check | Skip Reason |
|------|-------|-------------|
| 1. Existing PR | Fetch JIRA changelog via `mcp__jira__jira_batch_get_changelogs(issue_ids_or_keys=<key>)`, save JSON to `<WORKDIR>/fix-test-bugs/changelog-<key>.json`, then run `bash plugins/microshift-ci/scripts/check-jira-pr-links.sh <WORKDIR>/fix-test-bugs/changelog-<key>.json <key>`. The script checks both JIRA links and GitHub (fallback) internally, verifying PR state. Skip if it exits 0 (prints PR URL). CLOSED (unmerged) PRs do not block. | PR already exists |
| 2. In-scope files | Scan bug description for file paths in `test/`, `scripts/`, `docs/`. Also resolve bare filenames to their directory (e.g., `el98@rpm-standard1.sh` -> `test/scenarios/`, `configure-pri.sh` -> `scripts/multinode/`). Skip if ALL referenced files are outside the allowed directories (e.g., only `cmd/`, `pkg/`, `vendor/`). | Fix target outside allowed dirs |
| 3. Root cause is code-fixable | Skip if root cause indicates: product bug in MicroShift core (not test/script issue), transient environmental issue, or upstream dependency problem with no local workaround. Pass if root cause points to: test logic, timeout, configuration, assertion, variable resolution, checksum, script error handling, or documentation. | Not code-fixable |

## Implementation Steps

### Step 1: Fetch Bug Details

1. Parse `<ARGUMENTS>` to extract JIRA keys and flags (`--open`, `--fix`)
2. Validate:
   - If neither `--open` nor explicit bug keys were provided, show error: "Error: must specify either --open or one or more USHIFT bug keys" and stop
   - If `--open` is present with explicit keys, show error and stop
3. If `--open` was passed, query JIRA to discover keys:

   ```text
   mcp__jira__jira_search(jql='labels = "microshift-ci-ai-generated" AND resolution = Unresolved', fields='summary', limit=50)
   ```

   Extract issue keys from the results. If none found, report "No unresolved AI-generated bugs found" and stop.

4. For each JIRA key, fetch in parallel:
   - Bug details via `mcp__jira__jira_get_issue(issue_key=<key>)`
   - Changelog via `mcp__jira__jira_batch_get_changelogs(issue_ids_or_keys=<key>)`
5. Save each changelog to `<WORKDIR>/fix-test-bugs/changelog-<key>.json` and run `bash plugins/microshift-ci/scripts/check-jira-pr-links.sh <WORKDIR>/fix-test-bugs/changelog-<key>.json <key>` to check for JIRA-linked PRs
6. Apply Gates 1-3 to each bug
7. Record status: `eligible` or `skipped` (with gate and reason)

### Step 2: Present Dry-Run Report

For each bug, show:

```text
N. [WOULD FIX / SKIPPED] <USHIFT-XXXX>: <summary>
   Files: <identified test/ files>
   Fix approach: <from bug description>
   Reason: <skip reason if skipped>
```

Summary counters:

```text
SUMMARY
  Total: N | Eligible: N | Skipped: N
  Skip breakdown: PR exists=N, outside allowed dirs=N, not code-fixable=N
```

Save the report to `<WORKDIR>/report-fix-test-bugs.txt`.

If no `--fix` flag, stop here.

### Step 3: Attempt Fix (per eligible bug)

Process eligible bugs **sequentially** (one at a time — the single working tree is reused). For each eligible bug:

1. **Clone repo** (once, before first fix):

   ```text
   bash plugins/microshift-ci/scripts/fix-test-bugs.sh clone --workdir <WORKDIR>
   ```

2. **Create branch**:

   ```text
   bash plugins/microshift-ci/scripts/fix-test-bugs.sh branch --workdir <WORKDIR> --jira-key USHIFT-XXXX
   ```

3. **Apply fix** (LLM step): Read the identified files in `<WORKDIR>/microshift/`, understand the failure from the bug description, and make targeted edits.

   **Constraints** (MUST follow):
   - ONLY modify files under `test/`, `scripts/`, `docs/`
   - Maximum 5 files per fix
   - Minimal, targeted changes — fix only the reported problem
   - Preserve existing code style and conventions
   - Do NOT refactor, clean up, or improve surrounding code

4. **Verify, commit, push, and create PR**:

   ```text
   bash plugins/microshift-ci/scripts/fix-test-bugs.sh submit --workdir <WORKDIR> --jira-key USHIFT-XXXX --summary "<short description>" --rationale "<explanation of why this fix was chosen>"
   ```

   The `--rationale` should explain the root cause analysis and why this specific change fixes the problem (e.g., "MicroShift rotates short-lived certs when within 120 days of expiry. The old clock advance of 150 days didn't reach the rotation threshold at day 246. Increasing to 400 days puts the clock well past both the threshold and cert expiry.").

   The script performs safety verification (allowed dirs, max files), commits, pushes, and creates a draft PR against `main`. On any safety check failure, it reverts all changes and exits non-zero — record the bug as FAILED.

### Step 4: Report

Display results per bug:

```text
RESULTS
  1. USHIFT-1234: FIXED — https://github.com/openshift/microshift/pull/NNNN
  2. USHIFT-5678: SKIPPED — PR already exists
  3. USHIFT-9012: FAILED — changes outside allowed directories

SUMMARY
  Total: 3 | Fixed: 1 | Skipped: 1 | Failed: 1
```

Save the report to `<WORKDIR>/report-fix-test-bugs.txt` (overwrites the dry-run report from Step 2).

## Notes

- All PRs target `main` — backporting to release branches is left to the human reviewer
- The `fix-test-bugs.sh` script enforces safety guardrails deterministically: changes outside `test/`/`scripts/`/`docs/` are rejected, max 5 files
- Each bug gets its own branch (named after the JIRA key), so fixes are independently reviewable
- If a fix attempt fails (safety check, empty diff, push error), the script reverts all changes so the next bug starts clean
- The skill does NOT update JIRA issues — it only reads bug details

## Related Skills

- **microshift-ci:create-bugs**: Creates JIRA bugs from CI analysis — produces the bug keys consumed by this skill
- **microshift-ci:doctor**: Produces the CI analysis that feeds into bug creation
