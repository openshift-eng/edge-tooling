# release-health: Feature Fetcher

## Purpose

Fetch Features and Initiatives from OCPSTRAT for the given OCP release version and write `features.json` to the work directory.

## When to Spawn

The parent release-health skill spawns this agent during Phase 2, in parallel with the Sprint Mapper, to collect the release scope before any analysis begins.

## Capabilities

- Jira MCP search queries (`jira_search`)
- JSON file writing via `Write` tool

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

If no results, use the fallback and set `fallback_used: true`:

```jql
project = OCPSTRAT AND labels = "ocpedge-plan" AND status not in (Done, Closed) ORDER BY priority ASC
```

Paginate with `startAt=0`, `limit=50` until all results are fetched.

Requested fields:

```text
key, summary, status, issuetype, priority, assignee, fixVersions, labels, description,
issuelinks, customfield_10795, customfield_10470, customfield_10473, customfield_10475
```

### 2. Extract Fields Per Issue

For each result:

- `key`, `summary`
- `type`: `issuetype.name`
- `status`: `status.name`
- `sme`: `customfield_10475` → displayName (or `"None"`)
- `qa_contact`: `customfield_10470` → displayName (or `"None"`)
- `docs_approver`: `customfield_10473` → displayName (or `"None"`, or `"Field not configured"` if the field errors)
- `has_ac`: If `description` is null or undefined, set `false`. If `description` is an ADF object (has a `content` key), recursively extract all `text` leaf node values and join them, then check if the result contains `"Acceptance Criteria"` or `"AC:"`. If `description` is a plain string, check directly.
- `size`: `customfield_10795` value (or `"Unsized"`) — this is the T-shirt size field for OCPSTRAT Features/Initiatives (XS/S/M/L/XL)
- `spike_candidates`: issuelinks where `type.inward = "is blocked by"` AND linked issue type = `"Spike"`
  → list of `{ "key": "<OCPEDGE-XXX>", "status": "<status>", "issuelinks": [<full issuelinks array>] }`

### 3. Write Output

Write ONLY this JSON to `{WORKDIR}/features.json`:

```json
{
  "fallback_used": <bool>,
  "feature_keys": ["OCPSTRAT-XXX", ...],
  "feature_keys_csv": "OCPSTRAT-XXX, OCPSTRAT-YYY, ...",
  "features": [
    {
      "key": "OCPSTRAT-XXX",
      "summary": "...",
      "type": "Feature|Initiative",
      "status": "...",
      "sme": "...",
      "qa_contact": "...",
      "docs_approver": "...",
      "has_ac": <bool>,
      "size": "...",
      "spike_candidates": [{ "key": "...", "status": "...", "issuelinks": [] }]
    }
  ]
}
```
