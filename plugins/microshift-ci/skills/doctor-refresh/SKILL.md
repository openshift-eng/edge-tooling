---
name: microshift-ci:doctor-refresh
argument-hint: <release1,release2,...>
description: Regenerate the CI Doctor HTML report from existing data
user-invocable: true
allowed-tools: Bash, Read, Glob
---

# microshift-ci:doctor-refresh

## Synopsis

```bash
/microshift-ci:doctor-refresh <release1,release2,...>
```

## Description

Regenerates the CI Doctor HTML report from existing data. Use this after `/microshift-ci:create-bugs --create` to update the report with newly created bugs.

This is a lightweight operation: it does not re-analyze jobs, re-aggregate summaries, or re-query JIRA. It reads the existing bug mapping files (which include newly created bugs via the create-bugs Step 4c update) and regenerates the HTML.

## Arguments

- `<ARGUMENTS>` (required): Comma-separated list of release versions (e.g., `4.19,4.20,4.21,4.22`)

## Work Directory

Compute once at the start by running `date +%y%m%d` and substituting into the path below. In all commands, replace `<WORKDIR>` with the computed path.

```text
/tmp/microshift-ci-claude-workdir.<YYMMDD>
```

## Implementation Steps

### Step 1: Determine Workdir

1. Compute today's `<WORKDIR>` by running `date +%y%m%d` and substituting into `/tmp/microshift-ci-claude-workdir.<YYMMDD>`.
2. Verify the directory exists. If it does not:

   ```text
   Error: no workdir found at <WORKDIR>
   Run the full doctor workflow first: /microshift-ci:doctor <releases>
   ```

### Step 2: Verify Bug Mapping Files

1. Parse `<ARGUMENTS>` into a list of release versions.
2. Check for rebase PR sources in the workdir by looking for `analyze-ci-bug-candidates-rebase-release-*.json` files. Extract the source identifiers (e.g., `rebase-release-4.22`).
3. Verify that `<WORKDIR>/analyze-ci-bugs-<source>.json` exists for each release version and each rebase PR source.
4. If any mapping files are missing, report which ones are missing and show an error:

   ```text
   Error: bug mapping files missing for: <sources>
   Run the full create-bugs workflow first: /microshift-ci:create-bugs <sources> --create
   ```

   Continue to Step 3 anyway — the HTML report will be generated with whatever data is available.

**Do NOT** delete bug mapping files. **Do NOT** launch create-bugs agents. The mapping files are produced by the preceding `/microshift-ci:create-bugs` session and include newly created bugs (via Step 4c of the create-bugs skill).

### Step 3: Regenerate HTML Report

Run the refresh script:

```text
bash plugins/microshift-ci/scripts/doctor.sh refresh --component microshift --workdir <WORKDIR> <ARGUMENTS>
```

### Step 4: Report Completion

Display the path to the regenerated HTML report.

## Examples

### Example 1: Refresh After Bug Creation

```bash
/microshift-ci:doctor-refresh 4.19,4.20,4.21,4.22
```

### Example 2: Refresh a Single Release

```bash
/microshift-ci:doctor-refresh 4.22
```

## Prerequisites

- An existing workdir from a prior `/microshift-ci:doctor` run
- Bug mapping files from a prior `/microshift-ci:create-bugs` run

## Related Skills

- **microshift-ci:doctor**: Full CI analysis workflow (produces the initial HTML report)
- **microshift-ci:create-bugs**: Bug correlation and creation (produces the bug mapping files consumed by this skill)

## Notes

- This skill does NOT re-analyze jobs, re-aggregate summaries, or re-query JIRA — it only regenerates the HTML from existing data
- Bug mapping files must already exist from a prior `/microshift-ci:create-bugs` run
- Newly created bugs are included because the create-bugs skill updates the mapping files after creation (Step 4c)
