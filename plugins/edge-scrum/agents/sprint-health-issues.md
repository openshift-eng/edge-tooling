# Sprint Health: Issue Fetcher

## Purpose

Fetch all issues in the target sprint and write `sprint_issues.json` to the work directory.

## When to Spawn

Spawned by the sprint-health skill during Phase 2b, after sprint-mapper has completed and `SPRINT_ID` has been extracted from `sprints.json → target_sprint.id`.

## Capabilities

- Jira MCP queries (`jira_search`)
- JSON file writing via `Write` tool

This agent does **not** modify any Jira data.

## Parameters

| Placeholder | Description |
|---|---|
| `{WORKDIR}` | Work directory path |
| `{SPRINT_ID}` | Jira integer sprint ID (from `sprints.json → target_sprint.id`) |
| `{SPRINT_START}` | Sprint start date `YYYY-MM-DD` (available for context; staleness uses today as the reference) |

## Instructions

### 1. Fetch Sprint Issues

Call `jira_search` with:

**JQL:**

```
project in (OCPEDGE, USHIFT, OCPBUGS) AND sprint = {SPRINT_ID} ORDER BY priority ASC
```

**Fields:** `key, summary, description, status, issuetype, assignee, created, updated, labels, issuelinks, customfield_10028, customfield_10014, customfield_10021, customfield_10470`

Paginate (`start_at=0`, `limit=50`) until all results are fetched.

### 2. Transform Issues

Before transforming issues, read `{WORKDIR}/sprints.json` and extract `target_sprint.name` — use this as `sprint_name` in the output.

For each issue, build this object:

- `key`: issue key (e.g., `"OCPEDGE-123"`)
- `summary`: issue summary string
- `type`: `issuetype.name` — normalize to one of `Story`, `Bug`, `Spike`, `Task`. Map any unrecognized type to `"Task"`.
- `status`: `status.name`
- `assignee`: `assignee.name` (Jira Server/DC username field; or `null` if unassigned). Use the same value as the key in `sp_by_assignee`.
- `sp`: story points from `customfield_10028`. **Bugs always get `sp = 0` regardless of field value.**
- `epic_key`: extract from `customfield_10014` — if the value is a string, use it directly; if it is an object, use `customfield_10014.key`; if null or absent, use `"No Epic"`
- `flagged`: `true` if `customfield_10021` is a non-empty array, otherwise `false`
- `blocked_by`: array of issue keys from `issuelinks` where `type.inward = "is blocked by"` AND the linked issue's `status.name` is NOT in `{"Closed", "Verified", "Done", "Won't Fix"}`. Empty array if none.
- `stale`: `true` if `status.name` is in `{"In Progress", "Review"}` AND `updated` is more than 5 business days before today. Otherwise `false`.
- `created`: `created` field as `YYYY-MM-DD`
- `updated`: `updated` field as `YYYY-MM-DD`
- `has_ac`: `true` if the issue's `description` field contains any of: `"Acceptance Criteria"`, `"acceptance criteria"`, `"AC:"`, `"ac:"`, or an ADF heading node whose text includes `"acceptance"` (case-insensitive). `false` otherwise.
- `labels`: array of label strings from `labels` field
- `customfield_10470` (qa_contact): fetched but not currently transformed — reserved for future use

### 3. Compute Aggregates

- `total_issues`: count of all issues in the `issues` array
- `sp_by_assignee`: `{ username: total_sp }` — sum of `sp` per assignee. Bugs contribute 0. Unassigned issues excluded.
- `issues_by_type`: `{ "Story": [keys], "Bug": [keys], "Spike": [keys], "Task": [keys] }`
- `issues_by_epic`: `{ "<epic_key>": [keys], "No Epic": [keys] }`
- `total_sp`: sum of all `sp` values
- `total_done_sp`: sum of `sp` for issues where `status` is in `{"Done", "Closed", "Verified"}`
- `total_remaining_sp`: `total_sp - total_done_sp`

If `jira_search` returns zero issues, write the output schema with `total_issues: 0`, `issues: []`, `sp_by_assignee: {}`, `issues_by_type: {"Story": [], "Bug": [], "Spike": [], "Task": []}`, `issues_by_epic: {"No Epic": []}`, `total_sp: 0`, `total_done_sp: 0`, `total_remaining_sp: 0`.

### 4. Write Output

Write ONLY this JSON to `{WORKDIR}/sprint_issues.json`:

```json
{
  "sprint_id": {SPRINT_ID},
  "sprint_name": "<str>",
  "total_issues": <int>,
  "issues": [
    {
      "key": "OCPEDGE-123",
      "summary": "...",
      "type": "Story|Bug|Spike|Task",
      "status": "...",
      "assignee": "<str or null>",
      "sp": <int>,
      "epic_key": "<str or 'No Epic'>",
      "flagged": <bool>,
      "blocked_by": ["<key>"],
      "stale": <bool>,
      "created": "YYYY-MM-DD",
      "updated": "YYYY-MM-DD",
      "has_ac": <bool>,
      "labels": ["<str>"]
    }
  ],
  "sp_by_assignee": { "<username>": <int> },
  "issues_by_type": { "Story": ["<key>"], "Bug": ["<key>"], "Spike": ["<key>"], "Task": ["<key>"] },
  "issues_by_epic": { "<epic_key>": ["<key>"], "No Epic": ["<key>"] },
  "total_sp": <int>,
  "total_done_sp": <int>,
  "total_remaining_sp": <int>
}
```
