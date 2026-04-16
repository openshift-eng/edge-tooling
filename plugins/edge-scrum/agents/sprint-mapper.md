# Sprint Mapper

## Purpose

Fetch sprint metadata for board 11479 and write `sprints.json` to the work directory. Supports an optional target sprint lookup for sprint-health, and an optional sprint range for release-health. Both parameters can be used independently.

## When to Spawn

Spawned during Phase 2 data collection by release-health (with range params) or sprint-health (with TARGET_SPRINT param).

## Capabilities

- Jira MCP sprint queries (`jira_get_sprints_from_board`)
- JSON file writing via `Write` tool

This agent does **not** modify any Jira data.

## Parameters

Substituted by the parent before spawning:

| Placeholder | Description |
|---|---|
| `{WORKDIR}` | Work directory path |
| `{FIRST_SPRINT}` | First sprint number (refinement sprint). Leave empty when not using release-health range. |
| `{LAST_SPRINT}` | Last sprint number (branch cut sprint). Leave empty when not using release-health range. |
| `{TOTAL_DEV_SPRINTS}` | Total dev sprints = LAST_SPRINT − FIRST_SPRINT. Leave empty when not using release-health range. |
| `{TARGET_SPRINT}` | Sprint number (e.g., `285`) or `"active"` to auto-detect. Leave empty for release-health path. |

## Instructions

### 1. Fetch Sprints

Call `jira_get_sprints_from_board` for board_id `"11479"`:

- `state="active"`
- `state="closed"` — paginate from `start_at=0`, `limit=50` until all results are fetched
- `state="future"`

For each result: extract the sprint number from the name (e.g., `"OCPEDGE Sprint 287"` → `287`).

**If `{FIRST_SPRINT}` and `{LAST_SPRINT}` are provided**: only include sprints where the number is between `{FIRST_SPRINT}` and `{LAST_SPRINT}` inclusive for the `sprint_map` and derived release-health fields.

**If `{FIRST_SPRINT}` is empty**: populate `sprint_map` with all fetched sprints (no range filter).

**If `{TARGET_SPRINT}` is provided**: use the full unfiltered result set to find the target sprint.

### 2. Compute Release-Health Derived Values

Sections 2 and 3 are independent. Execute whichever sections have non-empty trigger parameters — both can execute in the same run.

**Only when `{FIRST_SPRINT}` is non-empty:**

- `refinement_sprint_id`: ID of the sprint whose number = `{FIRST_SPRINT}`
- `refinement_sprint_closed`: `true` if that sprint's state = `"closed"`
- `current_sprint_num`: active sprint number in range; if none, highest closed sprint in range
- `completed_sprint_nums`: array of closed sprint numbers in range excluding `{FIRST_SPRINT}`
- `completed_dev_sprint_count`: count of `completed_sprint_nums`
- `remaining_sprints`: active + future sprints up to and including `{LAST_SPRINT}` (intermediate — not written to output)
- `remaining_sprint_count`: count of `remaining_sprints`
- `sprints_until_branch_cut`: same as `remaining_sprint_count`
- `expected_dev_completion_pct`: if `{TOTAL_DEV_SPRINTS} = 0` → `0`; else `completed_dev_sprint_count / {TOTAL_DEV_SPRINTS} × 100`

If no sprints are found for the range, set `"error": "No sprints found for range {FIRST_SPRINT}–{LAST_SPRINT}"`.

When `{FIRST_SPRINT}` is empty, set all release-health fields to `null`.

### 3. Compute Target Sprint

**Only when `{TARGET_SPRINT}` is non-empty:**

Find the matching sprint from the full unfiltered result set:

- `{TARGET_SPRINT}` = `"active"`: select the sprint with `state = "active"`. If none, select the highest-numbered closed sprint.
- Otherwise: select the sprint whose extracted number matches `{TARGET_SPRINT}`.

If no matching sprint is found, set `target_sprint` to `null`.

If found, compute:

- `days_elapsed`: calendar days from `start` to today (inclusive), capped at `total_days`
- `days_remaining`: calendar days from today to `end` (inclusive), minimum 0
- `total_days`: calendar days from `start` to `end` (inclusive)

Set `target_sprint` to:

```json
{
  "id": <int>,
  "name": "<str>",
  "goal": "<str or null>",
  "start": "<YYYY-MM-DD>",
  "end": "<YYYY-MM-DD>",
  "state": "<str>",
  "days_elapsed": <int>,
  "days_remaining": <int>,
  "total_days": <int>
}
```

`goal` comes from the Jira sprint object's `goal` field. Set to `null` if empty or absent.

When `{TARGET_SPRINT}` is empty, set `target_sprint` to `null`.

### 4. Write Output

Write ONLY this JSON to `{WORKDIR}/sprints.json`:

```json
{
  "sprint_map": {
    "<num>": { "id": <int>, "name": "<str>", "start": "<YYYY-MM-DD>", "end": "<YYYY-MM-DD>", "state": "<str>" }
  },
  "refinement_sprint_id": <int or null>,
  "refinement_sprint_closed": <bool or null>,
  "current_sprint_num": <int or null>,
  "completed_sprint_nums": [<int>] or null,
  "completed_dev_sprint_count": <int or null>,
  "total_dev_sprints": <int or null>,
  "remaining_sprint_count": <int or null>,
  "sprints_until_branch_cut": <int or null>,
  "expected_dev_completion_pct": <float or null>,
  "target_sprint": <object or null>
}
```
