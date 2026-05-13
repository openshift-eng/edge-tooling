---
name: microshift-ci:doctor-refresh
argument-hint: <release1,release2,...>
description: Re-run bug correlation and regenerate the CI Doctor HTML report
user-invocable: true
allowed-tools: Skill, Bash, Read, Write, Agent
---

# microshift-ci:doctor-refresh

## Synopsis

```bash
/microshift-ci:doctor-refresh <release1,release2,...>
```

## Description

Re-runs bug correlation (dry-run) for each release and regenerates the CI Doctor HTML report. Use this after `/microshift-ci:create-bugs --create` or any JIRA state change to update the report with the latest bug data.

This is a lightweight operation: it does not re-analyze jobs or re-aggregate summaries. It re-runs the JIRA searches in create-bugs (which also fetches the open bugs list) and regenerates the HTML from existing summary data.

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

### Step 2: Re-Run Bug Correlation

1. Parse `<ARGUMENTS>` into a list of release versions.
2. Check for rebase PR sources in the workdir by looking for `analyze-ci-bug-candidates-rebase-release-*.json` files. Extract the source identifiers (e.g., `rebase-release-4.22`).
3. Delete existing bug mapping files so create-bugs performs fresh JIRA queries:

   ```text
   rm -f <WORKDIR>/analyze-ci-bugs-*.json
   ```

4. Launch `/microshift-ci:create-bugs` in dry-run mode as **Agents** — one per release version, plus one per rebase PR source:

   **For release versions:**

   ```text
   Agent: subagent_type=general_purpose, prompt="Run /microshift-ci:create-bugs <version>"
   ```

   **For rebase PR sources:**

   ```text
   Agent: subagent_type=general_purpose, prompt="Run /microshift-ci:create-bugs rebase-release-<version>"
   ```

5. Launch **ALL** agents in a **single message** as **foreground** agents. They run concurrently.
6. Each agent produces `<WORKDIR>/analyze-ci-bugs-<source>.json` with fresh JIRA data including the open bugs list.

**Error Handling**:

- If create-bugs fails for a source, note the failure but continue with other sources and HTML generation.

### Step 3: Regenerate HTML Report

Run the refresh script:

```text
bash plugins/microshift-ci/scripts/doctor.sh refresh --workdir <WORKDIR> <ARGUMENTS>
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

- MCP Jira server must be configured (for bug correlation)
- An existing workdir from a prior `/microshift-ci:doctor` run

## Related Skills

- **microshift-ci:doctor**: Full CI analysis workflow (produces the initial HTML report)
- **microshift-ci:create-bugs**: Bug correlation and creation (used by Step 2 agents)

## Notes

- This skill does NOT re-analyze jobs or re-aggregate summaries — it only refreshes JIRA data and regenerates the HTML. Also useful after manual JIRA triage (closing, reassigning, or updating bugs)
- The create-bugs agents fetch the open bugs list as part of their normal JIRA queries, so no separate open bugs query is needed
- All agents are launched in a single parallel wave for speed
