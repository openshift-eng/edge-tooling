---
name: sprint-health-retro-analyzer
description: Analyze sprint delivery, churn, and goal attainment for retrospective
allowed-tools: Read, Write, Bash, mcp__plugin_edge-scrum_mcp-atlassian__jira_batch_get_changelogs
user-invocable: false
---

# Sprint Health: Retro Analyzer

## Purpose

Summarize sprint delivery, track churn, and assess sprint goal achievement for retrospective input.

## When to Spawn

Spawned by the sprint-health skill during Phase 3 when mode = `retro`.

## Capabilities

- Jira MCP changelog queries (`jira_batch_get_changelogs`)
- File reads via `Read` tool
- Markdown file writing via `Write` tool

## Parameters

| Placeholder | Description |
|---|---|
| `{WORKDIR}` | Work directory path |
| `{TODAY}` | Today's date (`YYYY-MM-DD`) |
| `{SPRINT_START}` | Sprint start date (`YYYY-MM-DD`) — baseline for churn detection |
| `{SPRINT_ID}` | Jira integer sprint ID — available for reference; churn matching uses `target_sprint.name` from `sprints.json` |

## Input Files

- `{WORKDIR}/sprints.json` — sprint metadata (`target_sprint`)
- `{WORKDIR}/sprint_issues.json` — all sprint issues with aggregates
- `plugins/edge-scrum/references/laws/07-workflow-states.md` — done/closed state definitions
- `plugins/edge-scrum/references/laws/09-sprint-policies.md` — churn rules (authoritative)

## Instructions

### 1. Read Inputs

Read all input files. Extract:

- From `sprints.json → target_sprint`: `name`, `goal`, `start`, `end`
- From `sprint_issues.json`: `total_sp`, `total_done_sp`, issues array (key, summary, status, sp, assignee)
- From law files: churn rule — net SP added must be offset by equal removal; violations are flag-worthy

### 2. Delivery Summary

- Determine each issue's done state using the per-type definitions from `07-workflow-states.md`:
  - **Bug** with key starting with `OCPBUGS-`: done if `status` in `{"Verified", "Closed"}`
  - **All other types** (Story, Spike, Task, Bug with OCPEDGE/other key): done if `status` = `"Closed"`
- `committed_sp` = `total_sp`
- `delivered_sp` = sum of SP for issues matching their type's done state
- `delivery_rate` = `round(delivered_sp / committed_sp × 100, 1)` — use `0` if `committed_sp = 0`
- `spill_list`: issues NOT matching their type's done state — record: key, summary, assignee, SP
- `total_spill_sp`: sum of SP for all issues in `spill_list`

### 3. Churn Tracking

Call `jira_batch_get_changelogs` for all issue keys in `sprint_issues.json`.

Before scanning changelogs, read `{WORKDIR}/sprints.json` and extract `target_sprint.name` (e.g., `"OCPEDGE Sprint 285"`). Use this sprint name string for matching — Jira changelog Sprint field values contain the sprint name, not the integer ID.

For each changelog entry across all issues:

- Find field-level changes where `field = "Sprint"` and `created` (change timestamp) is AFTER `{SPRINT_START}`

- **Added mid-sprint**: the `toString` value contains `target_sprint.name` and `fromString` does not — issue was moved into this sprint after start
- **Removed mid-sprint**: the `fromString` value contains `target_sprint.name` and `toString` does not — issue was removed from this sprint after start

An issue that was both added and removed will appear in both lists — its SP will cancel out in `net_churn_sp`, which is the correct behavior. Do not deduplicate across the two lists.

For each added/removed issue, record: key, summary, SP, assignee, date of change (ISO date from changelog `created` field).

Compute:

- `added_sp` = sum of `sp` for added issues
- `removed_sp` = sum of `sp` for removed issues
- `net_churn_sp` = `added_sp - removed_sp`

Churn rule violation: if `net_churn_sp > 0`, flag as a violation — more SP was added than removed.

If `jira_batch_get_changelogs` is unavailable or returns no changelog data, still write the full CHURN section skeleton with the `===SECTION:CHURN===` sentinel, a top-line note "Churn data unavailable", and both Added and Removed subsections showing "None".

### 4. Sprint Goal Analysis

If `target_sprint.goal` is `null`: write "No sprint goal set; goal analysis skipped."

Otherwise: extract key nouns and verbs from the goal text. Match issue summaries that contain those terms (conservative matching — only flag an issue as goal-related if there is clear lexical overlap). List the issue keys you consider goal-related before assessing the result. If no issues match the goal text, note "No traceable issues found for this goal." Assess:

- **Fully met**: issues covering the goal are all done
- **Partially met**: some goal-related issues done, some spilled
- **Not met**: goal-related work not done or significantly spilled

Note contributing factors (e.g., churn? blockers? scope change?).

### 5. Write Output

Write structured markdown to `{WORKDIR}/analysis.md` using this exact structure. Include all sentinel lines exactly as shown.

```
===SECTION:DELIVERY===
## Delivery Summary

| Metric | Value |
|--------|-------|
| Committed SP | <committed_sp> |
| Delivered SP | <delivered_sp> |
| Delivery Rate | <delivery_rate>% |

**Spill (<count> issues, <total_spill_sp> SP):**

| Key | Summary | Assignee | SP |
|-----|---------|----------|-----|
| <key> | <summary> | <assignee> | <sp> |

<"None" if no spill.>

===SECTION:CHURN===
## Sprint Churn

**Added mid-sprint** (<count> issues, <added_sp> SP):

| Key | Summary | Assignee | SP | Date Added |
|-----|---------|----------|-----|-----------|
| <key> | <summary> | <assignee> | <sp> | <date> |

<"None" if no additions.>

**Removed mid-sprint** (<count> issues, <removed_sp> SP):

| Key | Summary | Assignee | SP | Date Removed |
|-----|---------|----------|-----|-------------|
| <key> | <summary> | <assignee> | <sp> | <date> |

<"None" if no removals.>

**Net churn**: +<added_sp> / -<removed_sp> = <net_churn_sp> SP

<⚠️ Churn violation: more SP was added than removed — flag for retro discussion. | "No churn violations.">

===SECTION:GOAL_ANALYSIS===
## Sprint Goal Analysis

**Goal**: <goal text or "Not set">
**Result**: Fully Met / Partially Met / Not Met / N/A

<2–4 sentence assessment. Name specific done/spilled issues that relate to the goal. Note contributing factors.>

===SECTION:ACTIONS===
## Retrospective Input

<Numbered list of retro items. What went well (delivery, goal achievement) and what to improve
(churn violations, low delivery rate). Name specific issues or patterns, not generic advice.>
```

## Verify

After writing `{WORKDIR}/analysis.md`, read it back and confirm:
- All four sentinel lines are present: `===SECTION:DELIVERY===`, `===SECTION:CHURN===`, `===SECTION:GOAL_ANALYSIS===`, `===SECTION:ACTIONS===` (note: HEADER section is NOT written by this agent — SKILL.md generates the header)
- The DELIVERY section contains the summary table and spill list
- The CHURN section contains both Added and Removed subsections (even if "None")
- The GOAL_ANALYSIS section contains a Result line (Fully Met / Partially Met / Not Met / N/A)
- The ACTIONS section contains at least one numbered item

Do NOT commit.

Report: **DONE**, **DONE_WITH_CONCERNS**, **NEEDS_CONTEXT**, or **BLOCKED**.
