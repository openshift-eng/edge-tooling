---
name: microshift-ci:fix-test-bugs
argument-hint: <4.22,5.0,main | USHIFT-1234[,USHIFT-5678,...]> [--fix]
description: Attempt to fix CI bugs by opening PRs in openshift/microshift (dry-run by default)
user-invocable: true
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Agent
---

# microshift-ci:fix-test-bugs

## Synopsis

```bash
/microshift-ci:fix-test-bugs 4.22,5.0,main
/microshift-ci:fix-test-bugs 4.22,5.0,main --fix
/microshift-ci:fix-test-bugs USHIFT-1234,USHIFT-5678
/microshift-ci:fix-test-bugs USHIFT-1234 --fix
```

## Description

Given a list of releases or explicit JIRA bug keys, loads the merged candidates file produced by `/microshift-ci:create-bugs`, groups related bugs by shared root cause, evaluates eligibility, and attempts automated fixes in `test/`, `scripts/`, or `docs/` of openshift/microshift, opening one PR per group against `main` for human review. Changes to product code (`cmd/`, `pkg/`, `vendor/`, etc.) are never attempted.

Operates in **dry-run mode by default** — shows which groups are eligible and what fixes would be attempted. Use `--fix` to perform actual fixes.

## Arguments

- `<ARGUMENTS>` (required): Must include either releases or explicit bug keys, optionally followed by `--fix`
  - `<releases>` (comma-separated): Release versions (e.g., `4.22,5.0,main`). Selects all candidates from the merged file whose `releases` array includes at least one of the specified versions. Releases are identified by numeric format or the literal `main`.
  - `<keys>` (comma-separated): One or more USHIFT bug keys (e.g., `USHIFT-1234` or `USHIFT-1234,USHIFT-5678`). Keys match `USHIFT-\d+` pattern.
  - `--fix` (optional): Attempt fixes. Without this flag, only a dry-run report is produced.

## Work Directory

Compute once at the start by running `date +%y%m%d` and substituting into the path below. In all commands, replace `<WORKDIR>` with the computed path — do not use shell variables.

```text
/tmp/microshift-ci-claude-workdir.<YYMMDD>
```

The repo clone lives at `<WORKDIR>/microshift/` (shared with other skills).

## Prerequisites

- An existing workdir from a prior `/microshift-ci:create-bugs` run (today's date)
- `bugs/bug-candidates-merged-*.json` must exist in the workdir (produced by `/microshift-ci:create-bugs`)
- `gh` CLI authenticated with PR creation permissions
- Git user must have a fork of openshift/microshift (for pushing branches)

## Eligibility Decision Tree

Evaluated in order per **group** (a group is one merged candidate and all its JIRA keys). Must pass all gates to be eligible.

**Allowed directories** for fixes: `test/`, `scripts/`, `docs/`

| Gate | Check | Skip Reason |
|------|-------|-------------|
| 1. Existing PR | Checked in batch via `fix-test-bugs.sh check` (see Step 1). Run with ALL keys from the group. If ANY key's array is non-empty (open or merged PR), skip the **entire group**. CLOSED (unmerged) PRs do not block. | PR already \<state\>: \<url\> (e.g., "PR already merged: https://...") |
| 2. In-scope files | Scan the candidate's `analysis_text` for file paths in `test/`, `scripts/`, `docs/`. Also resolve bare filenames to their directory (e.g., `el98@rpm-standard1.sh` -> `test/scenarios/`, `configure-pri.sh` -> `scripts/multinode/`). Skip if ALL referenced files are outside the allowed directories. | Fix target outside allowed dirs |
| 3. Root cause is code-fixable | Use the candidate's `failure_type` and `root_cause`. Skip if `failure_type` is `infrastructure`. Skip if root cause indicates: product bug in MicroShift core (not test/script issue), transient environmental issue, or upstream dependency problem with no local workaround. Pass if root cause points to: test logic, timeout, configuration, assertion, variable resolution, checksum, script error handling, or documentation. | Not code-fixable |

## Implementation Steps

### Step 0: Load Candidates and Build Groups

1. Parse `<ARGUMENTS>` to determine mode (releases vs. explicit keys) and flags (`--fix`)
2. Validate: if no releases and no keys provided, show error and stop
3. Read `<WORKDIR>/bugs/bug-candidates-merged-*.json`. If no such file exists, report a **fatal error** and stop:

   ```text
   Error: no merged candidates file found in <WORKDIR>/bugs/
   Run /microshift-ci:create-bugs first to generate the merged candidates.
   ```

   If multiple files exist, read ALL of them and combine their candidates arrays.

4. Build groups from candidates — each candidate with JIRA keys becomes one group:
   - If **releases** given (e.g., `4.22,5.0`): filter candidates to those whose `releases` array contains at least one of the specified release versions, AND whose `duplicates` array is non-empty. Each matching candidate = one group; the group's keys are the keys from `duplicates`.
   - If **explicit USHIFT keys**: scan all candidates' `duplicates` arrays to find which candidates contain any of the given keys. Multiple explicit keys mapping to the same candidate form one group. Keys not found in any candidate are an error — report them and stop.

5. For each group, sort the keys by numeric suffix (ascending) and select the **primary key** — the first (lowest-numbered) USHIFT key. Pass the sorted keys to all `fix-test-bugs.sh` commands so the script's first-key extraction matches.

### Step 1: Evaluate Eligibility

**Batch PR check** (Gate 1 for all groups at once): Collect all keys from all groups into a single call:

   ```text
   bash plugins/microshift-ci/scripts/fix-test-bugs.sh check --jira-keys KEY1,KEY2,...
   ```

   Returns JSON: `{"KEY1": [{"url": "...", "state": "open|merged"}, ...], "KEY2": []}`. If ANY key in a group has a non-empty array, skip the entire group. Use the url and state from the result in the skip reason (e.g., "PR already merged: https://...").

Apply Gates 2–3 to remaining groups and record status: `eligible` or `skipped` (with gate and reason).

- **Gate 2**: Scan the candidate's `analysis_text` for file paths. Use the union of all referenced files across the group.
- **Gate 3**: Use `failure_type` and `root_cause` from the candidate.

### Step 2: Present Dry-Run Report

For each group, show:

```text
GROUP N (<count> bugs): [WOULD FIX / SKIPPED]
  Primary: USHIFT-XXXX: <error_signature>
  Related: USHIFT-YYYY, USHIFT-ZZZZ
  Releases: 4.20, 4.21, 4.22
  Files: <identified test/ files from analysis_text>
  Root cause: <root_cause from candidate>
  Fix approach: <brief description>
  Reason: <skip reason if skipped>
```

For single-bug groups, omit the "Related" line.

Summary counters:

```text
SUMMARY
  Total groups: N (M bugs) | Eligible: N (M bugs) | Skipped: N (M bugs)
  Skip breakdown: PR exists=N, outside allowed dirs=N, not code-fixable=N
```

Save the report to `<WORKDIR>/report-fix-test-bugs.txt`.

If no `--fix` flag, stop here.

### Step 3: Attempt Fix (per eligible group)

Process eligible groups **sequentially** (one at a time — the single working tree is reused). For each eligible group:

1. **Clone repo** (once, before first fix):

   ```text
   bash plugins/microshift-ci/scripts/fix-test-bugs.sh clone --workdir <WORKDIR>
   ```

2. **Create branch**:

   ```text
   bash plugins/microshift-ci/scripts/fix-test-bugs.sh branch --workdir <WORKDIR> --jira-keys USHIFT-XXXX,USHIFT-YYYY
   ```

   The branch is named after the primary (first) key.

3. **Apply fix** (LLM step): Read the identified files in `<WORKDIR>/microshift/`, understand the failure from the candidate's `analysis_text` and `root_cause`, and make targeted edits.

   **Constraints** (MUST follow):
   - ONLY modify files under `test/`, `scripts/`, `docs/`
   - Maximum 5 files per fix
   - Minimal, targeted changes — fix only the reported problem
   - Preserve existing code style and conventions
   - Do NOT refactor, clean up, or improve surrounding code

4. **Verify, commit, push, and create PR**:

   ```text
   bash plugins/microshift-ci/scripts/fix-test-bugs.sh submit --workdir <WORKDIR> --jira-keys USHIFT-XXXX,USHIFT-YYYY --summary "<short description>" --rationale "<explanation of why this fix was chosen>"
   ```

   The `--rationale` should explain the root cause analysis and why this specific change fixes the problem.

   The script performs safety verification (allowed dirs, max files), commits, pushes, and creates a draft PR against `main`. The PR title and body reference all JIRA keys in the group. On any safety check failure, it reverts all changes and exits non-zero — record the group as FAILED.

### Step 4: Report

Display results per group:

```text
RESULTS
  1. [USHIFT-7107,USHIFT-7138,USHIFT-7139]: FIXED — https://github.com/openshift/microshift/pull/NNNN
  2. [USHIFT-5678]: SKIPPED — PR already exists
  3. [USHIFT-9012,USHIFT-9013]: FAILED — changes outside allowed directories

SUMMARY
  Total groups: N (M bugs) | Fixed: N (M bugs) | Skipped: N (M bugs) | Failed: N (M bugs)
```

Save the report to `<WORKDIR>/report-fix-test-bugs.txt` (overwrites the dry-run report from Step 2).

## Notes

- All PRs target `main` — backporting to release branches is left to the human reviewer
- The `fix-test-bugs.sh` script enforces safety guardrails deterministically: changes outside `test/`/`scripts/`/`docs/` are rejected, max 5 files
- Each group gets its own branch (named after the primary JIRA key), so fixes are independently reviewable
- If a fix attempt fails (safety check, empty diff, push error), the script reverts all changes so the next group starts clean
- The skill does NOT update JIRA issues — it only reads the merged candidates file
- The candidate's `analysis_text` contains the full prose analysis from CI job reports — this is richer context than the JIRA bug description (which was derived from it)

## Related Skills

- **microshift-ci:create-bugs**: Creates JIRA bugs from CI analysis — produces the merged candidates file consumed by this skill
- **microshift-ci:close-stale-bugs**: Closes stale bugs that no longer match current CI failures
- **microshift-ci:doctor**: Produces the CI analysis that feeds into bug creation
