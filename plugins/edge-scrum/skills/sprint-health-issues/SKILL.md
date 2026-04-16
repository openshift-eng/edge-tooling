---
name: sprint-health-issues
description: Fetch sprint issues from Jira and write sprint_issues.json
allowed-tools: Read, Write, Bash, mcp__plugin_edge-scrum_mcp-atlassian__jira_search
user-invocable: false
---

# Sprint Health: Issue Fetcher

## Purpose

Fetch all issues in the target sprint and write `sprint_issues.json` to the work directory using the `transform-sprint-issues.py` script.

## When to Spawn

Spawned by the sprint-health skill during Phase 2b, after sprint-mapper has completed and `SPRINT_ID` has been extracted from `sprints.json → target_sprint.id`.

## Capabilities

- Jira MCP queries (`jira_search`)
- Data transformation via `transform-sprint-issues.py`

This agent does **not** modify any Jira data.

## Parameters

| Placeholder | Description |
|---|---|
| `{WORKDIR}` | Work directory path |
| `{SPRINT_ID}` | Jira integer sprint ID (from `sprints.json → target_sprint.id`) |
| `{SPRINT_START}` | Sprint start date `YYYY-MM-DD` |
| `{TODAY}` | Today's date as YYYY-MM-DD |

## Instructions

### 1. Read Sprint Name

Read `{WORKDIR}/sprints.json` and extract `target_sprint.name`.

### 2. Fetch Sprint Issues

Call `jira_search` with:

**JQL:**

```
project in (OCPEDGE, USHIFT, OCPBUGS) AND sprint = {SPRINT_ID} ORDER BY priority ASC
```

**Fields:** `key, summary, description, status, issuetype, assignee, created, updated, labels, issuelinks, customfield_10028, customfield_10014, customfield_10021, customfield_10470`

Paginate (`start_at=0`, `limit=50`) until all results are fetched.

### 3. Save Raw Responses

After each MCP call, write the raw JSON response to:

- `{WORKDIR}/raw_issues_0.json`, `raw_issues_1.json`, ... (one per page)

Use the `Write` tool. Write the **complete MCP tool response** as-is.

### 4. Run Transform Script

```bash
python3 plugins/edge-scrum/bin/transform-sprint-issues.py \
  --input {WORKDIR}/raw_issues_*.json \
  --output {WORKDIR}/sprint_issues.json \
  --sprint-id {SPRINT_ID} \
  --sprint-name "<sprint_name from step 1>" \
  --today {TODAY}
```

### 5. Verify Output

Read `{WORKDIR}/sprint_issues.json` and confirm:

- `total_issues` matches the number of issues fetched
- `total_sp` and aggregates are present

Report the issue count and SP summary.
