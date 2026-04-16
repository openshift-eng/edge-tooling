---
name: release-health-feature-fetcher
description: Fetch features and initiatives for release health analysis
allowed-tools: Read, Write, Bash, mcp__plugin_edge-scrum_mcp-atlassian__jira_search
user-invocable: false
---

# release-health: Feature Fetcher

## Purpose

Fetch Features and Initiatives from OCPSTRAT for the given OCP release version and write `features.json` to the work directory using the `transform-features.py` script.

## When to Spawn

The parent release-health skill spawns this agent during Phase 2, in parallel with the Sprint Mapper, to collect the release scope before any analysis begins.

## Capabilities

- Jira MCP search queries (`jira_search`)
- Data transformation via `transform-features.py`

This agent does **not** modify any Jira data.

## Parameters

Substituted by the parent before spawning:

| Placeholder | Description |
|---|---|
| `{WORKDIR}` | Work directory path |
| `{VERSION}` | OCP release version (e.g., `4.19`, `5.0`) |

## Instructions

### 1. Query Features and Initiatives

Try the primary JQL first:

```jql
project = OCPSTRAT AND labels = "ocpedge-plan" AND labels = "{VERSION}-candidate" ORDER BY priority ASC
```

If no results, use the fallback (note `fallback_used` for step 3):

```jql
project = OCPSTRAT AND labels = "ocpedge-plan" AND status not in (Done, Closed) ORDER BY priority ASC
```

Paginate with `startAt=0`, `limit=50` until all results are fetched.

Requested fields:

```text
key, summary, status, issuetype, priority, assignee, fixVersions, labels, description,
issuelinks, customfield_10795, customfield_10470, customfield_10473, customfield_10475
```

### 2. Save Raw Responses

After each MCP call, write the raw JSON response to:

- `{WORKDIR}/raw_features_0.json`, `raw_features_1.json`, ... (one per page)

Use the `Write` tool. Write the **complete MCP tool response** as-is.

### 3. Run Transform Script

```bash
python3 plugins/edge-scrum/bin/transform-features.py \
  --input {WORKDIR}/raw_features_*.json \
  --output {WORKDIR}/features.json
```

If the fallback JQL was used, append `--fallback-used`.

### 4. Verify Output

Read `{WORKDIR}/features.json` and confirm:

- `features` array is non-empty
- `feature_keys` and `feature_keys_csv` are populated

Report the feature count and whether fallback was used.
