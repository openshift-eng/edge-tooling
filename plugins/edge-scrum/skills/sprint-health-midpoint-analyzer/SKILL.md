---
name: sprint-health-midpoint-analyzer
description: Analyze mid-sprint burndown, blockers, and risk
allowed-tools: Read, Write, Bash
user-invocable: false
---

# Sprint Health: Mid-Sprint Analyzer

## Purpose

Assess burndown status, blockers, and sprint goal risk at the midpoint of a sprint.

## When to Spawn

Spawned by the sprint-health skill during Phase 3 when mode = `mid-sprint`.

## Capabilities

- File reads via `Read` tool
- Markdown file writing via `Write` tool

This agent makes **no additional Jira queries**.

## Parameters

| Placeholder | Description |
|---|---|
| `{WORKDIR}` | Work directory path |
| `{TODAY}` | Today's date (`YYYY-MM-DD`) |

## Input Files

- `{WORKDIR}/sprints.json` — sprint metadata (`target_sprint`)
- `{WORKDIR}/sprint_issues.json` — all sprint issues with aggregates

## Instructions

### 1. Read Inputs

Read both input files. Extract:

- From `sprints.json → target_sprint`: `name`, `goal`, `start`, `end`, `days_elapsed`, `days_remaining`, `total_days`
- From `sprint_issues.json`: `total_sp`, `total_done_sp`, `total_remaining_sp`, issues array

### 2. Burndown Analysis

Compute:

- `expected_done_sp` = `round(total_sp × (days_elapsed / total_days), 1)`
- `actual_done_sp` = `total_done_sp`
- `delta` = `actual_done_sp - expected_done_sp` (positive = ahead, negative = behind)
- Status:
  - On Track: `delta >= 0`
  - Slightly Behind: `delta < 0` and `abs(delta) ≤ total_sp × 0.1`
  - Behind: `delta < 0` and `abs(delta) > total_sp × 0.1`
- Projected finish: current pace = `total_done_sp / days_elapsed` SP per day. If `total_done_sp = 0` and `days_elapsed > 0`, report "No velocity — unable to project finish." Otherwise, days needed for remaining = `total_remaining_sp / pace`. Compare to `days_remaining`. Express as "on time" or "N days overrun."

If `days_elapsed = 0`, report burndown as "Sprint not yet started."
If `total_sp = 0`, report "No SP committed — burndown not applicable."

### 3. Blockers and Stalled Issues

From the issues array, collect issues matching any of:

- **Flagged**: `flagged = true`
- **Blocked**: `blocked_by` is a non-empty array
- **Blocked label**: `"Blocked"` in `labels`
- **Stale**: `stale = true`

Deduplicate across categories. For each, record: `key`, `summary`, `assignee`, reason(s) (comma-separated if multiple).

### 4. Sprint Goal Risk

If `target_sprint.goal` is `null`: note "No sprint goal set."

Otherwise: identify goal-related issues by extracting key nouns and verbs from the goal text and checking whether each issue's `summary` contains those terms. Use conservative matching — only flag an issue as goal-related if there is a clear lexical overlap. List the issue keys you consider goal-related before assessing risk. Assess:

- Are any goal-related issues in the blockers/stalled list?
- Is burndown on track?
- Emit risk level:
  - 🟢 if no goal-related issues are blocked/stale AND burndown is On Track
  - 🟡 if minor issues (stale but not blocked, or burndown Slightly Behind, but goal-related work is in progress)
  - 🔴 if goal-related work is blocked or unstarted, or burndown is Behind

### 5. Write Output

Write structured markdown to `{WORKDIR}/analysis.md` using this exact structure. Include all sentinel lines exactly as shown.

```
===SECTION:BURNDOWN===
## Burndown

| Metric | Value |
|--------|-------|
| Total SP | <total_sp> |
| Expected Done | <expected_done_sp> SP |
| Actual Done | <actual_done_sp> SP |
| Delta | <+/- delta> SP |
| Status | On Track / Slightly Behind / Behind |
| Projected Finish | <on time / N days overrun> |

===SECTION:BLOCKERS===
## Blockers and Stalled Issues

<Table with columns: Key, Summary, Assignee, Reason — or "None" if no blockers>

===SECTION:GOAL_RISK===
## Sprint Goal Risk

**Goal**: <goal text or "Not set">
**Risk**: 🟢/🟡/🔴

<2–4 sentence assessment explaining the risk level and what is or isn't at risk.>

===SECTION:ACTIONS===
## Recommended Actions

<Numbered list prioritized by urgency. Focus on: unblocking specific issues (name them and their blocker),
re-scoping if burndown is Behind, specific goal-risk mitigations.>
```

## Verify

After writing `{WORKDIR}/analysis.md`, read it back and confirm:
- All four sentinel lines are present: `===SECTION:BURNDOWN===`, `===SECTION:BLOCKERS===`, `===SECTION:GOAL_RISK===`, `===SECTION:ACTIONS===` (note: HEADER section is NOT written by this agent — SKILL.md generates the header)
- The BURNDOWN section contains the metrics table
- The BLOCKERS section either lists issues in a table or states "None"
- The GOAL_RISK section contains a Risk line (🟢/🟡/🔴) or notes "No sprint goal set"
- The ACTIONS section contains at least one numbered item

Do NOT commit.

Report: **DONE**, **DONE_WITH_CONCERNS**, **NEEDS_CONTEXT**, or **BLOCKED**.
