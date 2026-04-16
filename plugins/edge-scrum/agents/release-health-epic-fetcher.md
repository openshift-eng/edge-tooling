# release-health: Epic Fetcher

## Purpose

Fetch all Epics linked to the Features and Initiatives in `features.json` and write `epics.json` to the work directory.

## When to Spawn

The parent release-health skill spawns this agent during Phase 3, in parallel with the Spike Finder, after Phase 2 (Sprint Mapper + Feature Fetcher) completes.

## Capabilities

- Jira MCP search queries (`jira_search`)
- File reading via `Read` tool
- JSON file writing via `Write` tool

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

If `feature_keys` has more than 50 entries, split into batches of 50 and run one query per batch, merging all results. For each batch, paginate with `startAt=0`, `limit=50` until all results are fetched:

```jql
project in (OCPEDGE, USHIFT) AND "Parent Link" in ({feature_keys_batch_csv}) ORDER BY priority ASC
```

Requested fields:

```text
key, summary, status, assignee, labels, description,
customfield_10028, customfield_10018, customfield_10470, customfield_10473, customfield_10475
```

### 3. Extract Fields Per Epic

- `key`, `summary`
- `status`: `status.name`
- `feature_key`: `customfield_10018` → key (or `"No Feature"` if null/empty)
- `assignee`: displayName (or `"Unassigned"`)
- `qa_contact`: `customfield_10470` → displayName (or `"None"`)
- `size`: `customfield_10028` value (or `"Unsized"`)
- `has_ac`: If `description` is null or undefined, set `false`. If `description` is an ADF object (has a `content` key), recursively extract all `text` leaf node values and join them, then check if the result contains `"Acceptance Criteria"` or `"AC:"`. If `description` is a plain string, check directly.

Build `feature_to_epics`: map each feature key → list of its child epic keys.

### 4. Write Output

Write ONLY this JSON to `{WORKDIR}/epics.json`:

```json
{
  "epic_keys": ["OCPEDGE-XXX", ...],
  "epic_keys_csv": "OCPEDGE-XXX, ...",
  "feature_to_epics": { "OCPSTRAT-XXX": ["OCPEDGE-YYY", ...] },
  "epics": [
    {
      "key": "...", "summary": "...", "status": "...", "feature_key": "...",
      "assignee": "...", "qa_contact": "...", "size": "...", "has_ac": <bool>
    }
  ]
}
```
