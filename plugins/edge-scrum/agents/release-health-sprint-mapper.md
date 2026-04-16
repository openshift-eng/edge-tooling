# release-health: Sprint Mapper

## Purpose

Fetch sprint metadata for the OCPEDGE release range and write `sprints.json` to the work directory.

## When to Spawn

The parent release-health skill spawns this agent during Phase 2, in parallel with the Feature Fetcher, to collect sprint data before any analysis begins.

## Capabilities

- Jira MCP sprint queries (`jira_get_sprints_from_board`)
- JSON file writing via `Write` tool

This agent does **not** modify any Jira data.

## Parameters

Substituted by the parent before spawning:

| Placeholder | Description |
|---|---|
| `{WORKDIR}` | Work directory path |
| `{FIRST_SPRINT}` | First sprint number (the refinement sprint) |
| `{LAST_SPRINT}` | Last sprint number (branch cut sprint) |
| `{TOTAL_DEV_SPRINTS}` | Total dev sprints = LAST_SPRINT − FIRST_SPRINT |

## Instructions

### 1. Fetch Sprints

Call `jira_get_sprints_from_board` for board_id `"11479"`:

- `state="active"`
- `state="closed"` — paginate from `start_at=0`, `limit=50` until all results are fetched
- `state="future"`

For each result: extract the sprint number from the name (e.g., `"OCPEDGE Sprint 287"` → `287`). Only include sprints where the number is between `{FIRST_SPRINT}` and `{LAST_SPRINT}` inclusive.

### 2. Compute Derived Values

- `refinement_sprint`: sprint with number = `{FIRST_SPRINT}`
- `refinement_sprint_id`: that sprint's Jira integer ID
- `refinement_sprint_closed`: true if that sprint's state = `"closed"`
- `current_sprint_num`: active sprint number; if no active sprint, use the highest closed sprint in range
- `completed_dev_sprints`: closed sprints in range excluding `{FIRST_SPRINT}`
- `remaining_sprints`: active + future sprints up to and including `{LAST_SPRINT}`
- `remaining_sprint_count`: count of remaining_sprints
- `sprints_until_branch_cut`: same as remaining_sprint_count
- `expected_dev_completion_pct`: if `{TOTAL_DEV_SPRINTS} = 0`, set to `0`; otherwise `completed_dev_sprints.count / {TOTAL_DEV_SPRINTS} × 100`

If no sprints are found at all, set `"error": "No sprints found for range {FIRST_SPRINT}–{LAST_SPRINT}"`.

### 3. Write Output

Write ONLY this JSON to `{WORKDIR}/sprints.json`:

```json
{
  "sprint_map": {
    "<num>": { "id": <int>, "name": "<str>", "start": "<YYYY-MM-DD>", "end": "<YYYY-MM-DD>", "state": "<str>" }
  },
  "refinement_sprint_id": <int>,
  "refinement_sprint_closed": <bool>,
  "current_sprint_num": <int>,
  "completed_sprint_nums": [<int>],
  "completed_dev_sprint_count": <int>,
  "total_dev_sprints": <int>,
  "remaining_sprint_count": <int>,
  "sprints_until_branch_cut": <int>,
  "expected_dev_completion_pct": <float>
}
```
