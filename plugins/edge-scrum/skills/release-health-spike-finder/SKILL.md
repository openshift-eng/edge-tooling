---
name: release-health-spike-finder
description: Find refinement spikes for release health analysis
allowed-tools: Read, Write, Bash, mcp__plugin_edge-scrum_mcp-atlassian__jira_search
user-invocable: false
---

# release-health: Spike Finder

## Purpose

Identify refinement spikes for each Feature/Initiative and write `spikes.json` to the work directory using the `transform-spikes.py` script.

## When to Spawn

The parent release-health skill spawns this agent during Phase 3, in parallel with the Epic Fetcher, after Phase 2 (Sprint Mapper + Feature Fetcher) completes.

## Capabilities

- Jira MCP search queries (`jira_search`)
- Data transformation via `transform-spikes.py`

This agent does **not** modify any Jira data.

## Parameters

Substituted by the parent before spawning:

| Placeholder | Description |
|---|---|
| `{WORKDIR}` | Work directory path |

## Instructions

### 1. Read Prerequisites

1. Read `{WORKDIR}/sprints.json`. Extract `refinement_sprint_id`.
2. Read `{WORKDIR}/features.json` to confirm features exist.

### 2. Fetch All Spikes in the Refinement Sprint

Paginate with `startAt=0`, `limit=50` until all results are fetched:

```jql
project in (OCPEDGE, USHIFT) AND issuetype = Spike AND sprint = {refinement_sprint_id}
```

Fields: `key, summary, status, assignee, issuelinks`

### 3. Save Raw Responses

After each MCP call, write the raw JSON response to:

- `{WORKDIR}/raw_spikes_0.json`, `raw_spikes_1.json`, ... (one per page)

Use the `Write` tool. Write the **complete MCP tool response** as-is.

### 4. Run Transform Script

```bash
python3 plugins/edge-scrum/bin/transform-spikes.py \
  --input {WORKDIR}/raw_spikes_*.json \
  --features-file {WORKDIR}/features.json \
  --sprints-file {WORKDIR}/sprints.json \
  --output {WORKDIR}/spikes.json
```

### 5. Verify Output

Read `{WORKDIR}/spikes.json` and confirm:

- `spike_map` is populated
- `summary` totals are present

Report the spike matching summary.
