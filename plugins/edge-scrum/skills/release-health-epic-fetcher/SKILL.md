---
name: release-health-epic-fetcher
description: Fetch epics for release health analysis
allowed-tools: Read, Write, Bash, mcp__plugin_edge-scrum_mcp-atlassian__jira_search
user-invocable: false
---

# release-health: Epic Fetcher

## Purpose

Fetch all Epics linked to the Features and Initiatives in `features.json` and write `epics.json` to the work directory using the `transform-epics.py` script.

## When to Spawn

The parent release-health skill spawns this agent during Phase 3, in parallel with the Spike Finder, after Phase 2 (Sprint Mapper + Feature Fetcher) completes.

## Capabilities

- Jira MCP search queries (`jira_search`)
- Data transformation via `transform-epics.py`

This agent does **not** modify any Jira data.

## Parameters

Substituted by the parent before spawning:

| Placeholder | Description |
|---|---|
| `{WORKDIR}` | Work directory path |

## Instructions

### 1. Read Prerequisites

Read `{WORKDIR}/features.json`. Extract `feature_keys_csv`.

### 2. Query Epics

If `feature_keys` has more than 50 entries, split into batches of 50 and run one query per batch. For each batch, paginate with `startAt=0`, `limit=50` until all results are fetched:

```jql
project in (OCPEDGE, USHIFT) AND "Parent Link" in ({feature_keys_batch_csv}) ORDER BY priority ASC
```

Requested fields:

```text
key, summary, status, assignee, labels, description,
customfield_10028, customfield_10018, customfield_10470, customfield_10473, customfield_10475
```

### 3. Save Raw Responses

After each MCP call, write the raw JSON response to:

- `{WORKDIR}/raw_epics_0.json`, `raw_epics_1.json`, ... (one per page/batch)

Use the `Write` tool. Write the **complete MCP tool response** as-is.

### 4. Run Transform Script

```bash
python3 plugins/edge-scrum/bin/transform-epics.py \
  --input {WORKDIR}/raw_epics_*.json \
  --output {WORKDIR}/epics.json
```

### 5. Verify Output

Read `{WORKDIR}/epics.json` and confirm:

- `epics` array is non-empty
- `feature_to_epics` mapping is populated

Report the epic count.
