---
name: sprint-mapper
description: Fetch sprint metadata from Jira board and write sprints.json
allowed-tools: Read, Write, Bash, mcp__plugin_edge-scrum_mcp-atlassian__jira_get_sprints_from_board
user-invocable: false
---

# Sprint Mapper

## Purpose

Fetch sprint metadata for board 11479 and write `sprints.json` to the work directory using the `transform-sprints.py` script.

## When to Spawn

Spawned during Phase 2 data collection by release-health (with range params) or sprint-health (with TARGET_SPRINT param).

## Capabilities

- Jira MCP sprint queries (`jira_get_sprints_from_board`)
- Data transformation via `transform-sprints.py`

This agent does **not** modify any Jira data.

## Parameters

Substituted by the parent before spawning:

| Placeholder | Description |
|---|---|
| `{WORKDIR}` | Work directory path |
| `{TODAY}` | Today's date as YYYY-MM-DD |
| `{FIRST_SPRINT}` | First sprint number (refinement sprint). Leave empty when not using release-health range. |
| `{LAST_SPRINT}` | Last sprint number (branch cut sprint). Leave empty when not using release-health range. |
| `{TOTAL_DEV_SPRINTS}` | Total dev sprints = LAST_SPRINT − FIRST_SPRINT. Leave empty when not using release-health range. |
| `{TARGET_SPRINT}` | Sprint number (e.g., `285`) or `"active"` to auto-detect. Leave empty for release-health path. |

## Instructions

### 1. Fetch Sprints

Call `jira_get_sprints_from_board` for board_id `"11479"` three times:

- `state="active"`
- `state="closed"` — paginate from `start_at=0`, `limit=50` until all results are fetched
- `state="future"`

### 2. Save Raw Responses

After each MCP call, write the raw JSON response to a file in the work directory:

- `{WORKDIR}/raw_sprints_active.json`
- `{WORKDIR}/raw_sprints_closed_0.json`, `raw_sprints_closed_1.json`, ... (one per page)
- `{WORKDIR}/raw_sprints_future.json`

Use the `Write` tool for each file. Write the **complete MCP tool response** as-is — do not extract or modify the JSON.

### 3. Run Transform Script

Build and run the command:

```bash
python3 plugins/edge-scrum/bin/transform-sprints.py \
  --input {WORKDIR}/raw_sprints_*.json \
  --output {WORKDIR}/sprints.json \
  --today {TODAY}
```

Append these flags only if the corresponding parameter is non-empty:

- `--target-sprint {TARGET_SPRINT}`
- `--first-sprint {FIRST_SPRINT} --last-sprint {LAST_SPRINT} --total-dev-sprints {TOTAL_DEV_SPRINTS}`

### 4. Verify Output

Read `{WORKDIR}/sprints.json` and confirm:

- `sprint_map` is non-empty
- If `{TARGET_SPRINT}` was provided: `target_sprint` is non-null
- If `{FIRST_SPRINT}` was provided: release-health fields are populated (not all null)

Report the sprint count and target sprint summary.
