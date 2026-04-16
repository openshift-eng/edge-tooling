# release-health: Spike Finder

## Purpose

Identify refinement spikes for each Feature/Initiative and write `spikes.json` to the work directory.

## When to Spawn

The parent release-health skill spawns this agent during Phase 3, in parallel with the Epic Fetcher, after Phase 2 (Sprint Mapper + Feature Fetcher) completes.

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

1. Read `{WORKDIR}/sprints.json`. Extract `refinement_sprint_id` and `refinement_sprint_closed`.
2. Read `{WORKDIR}/features.json`. Extract the `features` array (keys and `spike_candidates` per feature).

### 2. Fetch All Spikes in the Refinement Sprint

Paginate with `startAt=0`, `limit=50` until all results are fetched:

```jql
project in (OCPEDGE, USHIFT) AND issuetype = Spike AND sprint = {refinement_sprint_id}
```

Fields: `key, summary, status, assignee, issuelinks`

### 3. Match Spikes to Features

For each fetched spike, check `issuelinks` for a link where `type.outward = "blocks"` and the target key is in the features list. Combine with `spike_candidates` already found in `features.json`. Deduplicate by spike key.

### 4. Compute Per-Feature Spike State

For each feature:

- `spike_key`: matched spike key (or null)
- `spike_keys`: all matched spike keys (for multi-spike features)
- `spike_status`: status of the primary spike (or `"Missing"`)
- `spike_in_ref_sprint`: true if the spike is assigned to the refinement sprint
- `spike_overdue`: `refinement_sprint_closed AND spike_status != "Closed"`
- `spike_missing`: `spike_key == null`
- `spike_on_epic`: false (placeholder — the Analysis agent detects this after cross-referencing `epics.json`)
- `spike_on_epic_keys`: []

Include the full `issuelinks` array for each entry in `all_ref_sprint_spikes` — the Analysis agent uses these to detect spikes linked to child Epics instead of the Feature/Initiative directly.

### 5. Write Output

Write ONLY this JSON to `{WORKDIR}/spikes.json`:

```json
{
  "spike_map": {
    "<feature_key>": {
      "spike_key": "...|null",
      "spike_keys": [],
      "spike_status": "...",
      "spike_in_ref_sprint": <bool>,
      "spike_overdue": <bool>,
      "spike_missing": <bool>,
      "spike_on_epic": false,
      "spike_on_epic_keys": []
    }
  },
  "all_ref_sprint_spikes": [
    { "key": "...", "summary": "...", "status": "...", "issuelinks": [] }
  ],
  "summary": {
    "features_with_spike": <int>,
    "features_with_closed_spike": <int>,
    "features_missing_spike": <int>,
    "features_spike_on_epic": 0,
    "total_features": <int>
  }
}
```
