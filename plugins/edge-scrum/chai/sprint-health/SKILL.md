---
name: sprint-health
description: Read-only coordinator skill that reports team-level sprint health for the current sprint — capacity mode in sprint week 1, mid-sprint mode after, with a sprint-end view in the last week — with no prompts and no per-person sections
---

# Sprint Health (chai-bot coordinator)

This playbook is for the **chai-bot coordinator**, not for Claude Code. It reports
the health of the current OpenShift Edge sprint at **team level**, as the
sprint-health part of the weekly health report (edge-context decision 0018). It is
**read-only**: it edits nothing in Jira and asks no questions. The analysis runs
inline in the coordinator; there are no sub-agents.

## Tools

| Tool | Used for |
|------|----------|
| `scrum-context` skill | Current sprint, its week, and its availability |
| `list_jira_board_sprints` | Closed sprints on board 11479 (velocity) |
| `query_jira` | Sprint issues (paged; see Jira paging) |
| `get_jira_issue` | Issue links of blocked candidates (mid-sprint mode) |
| `sandbox_exec` (Python) | `plugins/edge-scrum/bin/` scripts on the fetched data |

Jira credentials are not available in the sandbox: fetch everything with the
tools above, then pass the text into `sandbox_exec`.

## Configuration

```yaml
board_id: 11479
projects: [OCPEDGE, USHIFT, OCPBUGS]
ROSTER_SOURCE: "openshift-eng/edge-context: people/team-roster.md"
fields:  # request fields by id only
  story_points: customfield_10028
  sprint:       customfield_10020
  qa_contact:   customfield_10470
  epic_link:    customfield_10014
```

## Jira paging

`query_jira` returns text with no total count. Fetch every query this way:

1. First page: `(<JQL>) ORDER BY key DESC` with `max_results: 50`.
2. While the results header — the first such line, after any "Create metadata"
   block — is `Showing the first 50 matching issues; more match:` (the page then
   ends with the "... More issues match" hint), fetch the next page:
   `(<JQL>) AND key < <last key on the page> ORDER BY key DESC`.
3. Stop when a page has the header `Found N issues (showing N):` or fewer than
   50 issues. Also stop when the number of a page's last key is not lower than
   the number of the previous page's last key (same project; the page did not
   advance): the fetch is then incomplete; do not fetch it again.
4. **More than one project:** Jira's `key <` compares keys within one project
   only. When a query over several projects does not fit on one page, page each
   project separately by adding `AND project = <P>`, and pass each project as its
   own manifest part.
5. The fetch is complete only if every page succeeded. Pass all pages to
   `chai_jira_adapter.adapt_fetch` (below); it rechecks headers, page sizes and
   key order and returns `fetch.complete`. If a call failed or a page was cut
   off, the fetch is **incomplete**: compute nothing from it and list it as left
   out, with the reason.
6. A "Create metadata …" block may come before the results. If it says
   `Invalid extra_fields entries: …`, those fields were not returned: the fetch
   counts as incomplete, except for Flagged (`customfield_10021`), which is then
   reported as an unavailable signal. Unset custom fields print no line, so a
   missing Flagged line means "not flagged".
7. Output may be wrapped as `{"result": "<text>"}`; the adapter unwraps it. A
   query with no results returns `No issues found for JQL: <jql>`: pass the JQL
   sent with each page as `"jql"` in the manifest page
   (`{"text": ..., "jql": "<query sent>"}`). The page counts as complete and
   empty only when that JQL matches the query sent; otherwise the fetch is
   incomplete.
8. When `query_jira` says a field value was shortened or stored, read it whole
   once (with `result_page` for a stored result, or with the `get_jira_issue`
   call the note names) and put the whole value in place of the shortened one
   in that page's text before passing it to the script. If it cannot be read
   whole, pass the page as it is: the adapter marks the fetch incomplete (for
   example `<KEY>: <field> value shortened`), and it is left out like any
   incomplete fetch, never guessed.
9. Labels `query_jira` prints that the adapter doesn't know are ignored with a
   warning; they never change a known field. A date that isn't readable is
   dropped, never guessed, and listed in the adapted fetch's `left_out`: add
   every fetch's `left_out` to the report's `missing` so it appears under
   "Left out".

```python
import chai_jira_adapter as ad
adapted = ad.adapt_fetch({
    "name": "sprint_issues",
    "parts": [{"project": "OCPEDGE", "pages": [{"text": page_1, "jql": "<the JQL sent>"},
                                               {"text": page_2, "jql": "<the JQL sent>"}]},
              {"project": "USHIFT", "pages": [{"text": page_1, "jql": "<the JQL sent>"}]}]},
    # or "pages": [...] for one part; "jql" is the exact query sent for that page (step 7)
    roster=roster)
# adapted["issues"] is jira_search-shaped; write {"issues": adapted["issues"]} to a file
# and pass it to the transform scripts as --input.
```

A failed tool call is passed as `{"error": "<message>"}` in its page slot.

## Steps

### 1. Context and mode

Run the **scrum-context** skill with no argument. Use `current_sprint` (name, id,
start, end, `week`) and `current_sprint_availability`.

- `current_sprint` "not available" → stop; sprint health is left out with that
  reason.
- Mode: `chai_report.sprint_mode(week)`: week 1 → **capacity**, week 2 or later →
  **mid-sprint**. No prompt. A missing or non-numeric week gives no mode: run
  neither analysis; sprint health is left out ("sprint week unknown").
- A projected current sprint (no id) → stop; sprint health is left out
  ("current sprint is not on the board yet").

### 2. Roster

Read `ROSTER_SOURCE` and parse it in the sandbox with
`chai_jira_adapter.parse_team_roster(markdown)`. It keeps roles containing
"Software Engineer" or "Software Quality Engineer" (any level), drops anyone under
"Not counted for sprint capacity", and maps each person to their Rover email
(profile id + `@redhat.com`). Write `{"members": ..., "contacts": ...}` to
`roster.json`. If the roster is unreadable, velocity is not available.

### 3. Sprint issues

`query_jira`, paged:

- **JQL:** `project in (OCPEDGE, USHIFT, OCPBUGS) AND sprint = <current sprint id>`
- **Fields:** `summary, status, issuetype, priority, assignee, created, updated,
  labels, parent, customfield_10028, customfield_10014, customfield_10470`

Adapt, then run `transform-sprint-issues.py` with a metadata file
(`{"sprint_id", "sprint_name", "sprint_start", "sprint_end"}`, JSON-encoded) and
`--today`. Flagged, acceptance criteria and the sprint goal are not part of
this report (chai-bot cannot read Flagged, descriptions come back cut, and the
board's sprint list has no goal field).

**Mid-sprint mode only — blocked items.** `query_jira`, paged:
`(project in (OCPEDGE, USHIFT, OCPBUGS) AND sprint = <id>) AND issueLinkType = "is blocked by" AND statusCategory != Done`.
For each returned key, call `get_jira_issue(<key>, fields="issuelinks")` and pass the texts to
`chai_jira_adapter.attach_issue_links(adapted, {key: text_or_None})` **before**
the transform. The adapter turns the "Linked Jira Issues" lines into the
`issuelinks` shape, and `transform-sprint-issues.py` applies the single
pipeline rule, `_jira_transforms.extract_blocked_by`: an issue is blocked when
at least one "is blocked by" issue is not in a done state (Closed, Verified,
Done or Won't Fix). The response's Requested fields area says
`issuelinks: (empty, or not a field of this issue)` (no links) or
`issuelinks: (set, but get_jira_issue cannot render this field's structure)`
(the "Linked Jira Issues" section is read); a missing, unknown or cut state
never means "no links". If `attach_issue_links` returns any problem (a failed
`get_jira_issue` call, a response for another issue, no usable issuelinks
state, a response cut off or not readable, or a block row that could not be
read), or the candidate fetch is incomplete, set `links_complete = False`: the
blocked signal is left out, not guessed.

### 4. Velocity (capacity mode)

Velocity is `compute-velocity.py`: the plain average of completed, team-owned SP
over the **last 3 closed sprints** (decision 0015). The current sprint is never
included.

1. `list_jira_board_sprints` for board 11479 (closed). Keep only the board's own
   sprints; skip any marked with another origin board. Take the three closed
   sprints with the highest number below the current sprint.
2. For each, `query_jira`, paged: `project in (OCPEDGE, USHIFT, OCPBUGS) AND
   sprint = <id>`, fields `summary, status, issuetype, assignee, resolutiondate,
   customfield_10028, customfield_10470, customfield_10020`. Adapt with the
   roster, then `transform-sprint-issues.py` with that sprint's metadata.
3. `compute-velocity.py --sprint-issues <the 3 files> --roster roster.json`.
4. `team_velocity, reason = chai_report.usable_velocity(velocity_json)`.

If any of the three fetches is incomplete, or `usable_velocity` returns a reason
(fewer than 3 sprints, or done team issues skipped for missing sprint and
resolution data, which would understate it), velocity is **not available**
(left out with the reason); never average fewer sprints or estimate.

Capacity: `chai_report.sprint_capacity(team_velocity, current_sprint_availability)`
= velocity × the current sprint's availability. When availability is not a
number, capacity is the velocity unadjusted, labelled **"not PTO-adjusted"**.

### 5. Analysis (inline)

Run `chai_report.sprint_health(sprint_issues, current_sprint, today, mode,
velocity, availability, skipped_signals, links_complete,
velocity_reason=velocity_reason)`, where `velocity_reason` is the reason
`usable_velocity` returned (None when velocity is usable); it becomes the reason
capacity is left out. The checks themselves are `_scrum_rules.sprint_findings`:

- **Capacity mode (week 1):** committed SP and issues, capacity, committed vs
  capacity, and per issue "unpointed" and "no epic" (owner: the assignee). Over
  capacity is a 🔴 risk and a team item for the Scrum Master ("re-plan the
  sprint"); unpointed and epic-less issues are 🟡.
- **Mid-sprint mode (weeks 2 and 3):** burndown (expected = total SP × elapsed ÷
  total days; On Track / Slightly Behind (≤ 10% of total) / Behind; Behind adds
  "re-scope the sprint" for the Scrum Master), projected finish, and **explicit
  blocks only** on open issues: "blocked by KEY" (an open "is blocked by" link)
  and the "Blocked label" / "Parked" labels. There is no stale (no recent
  update) check.
- **Sprint-end view (the sprint's last week, week 3):** in the same reply,
  delivered SP and % (done SP ÷ committed SP, law 07 done states) and likely
  carry-over: every open issue is listed as "carry-over" on its owner's line.

**Team level only.** There is no per-person load table, no per-person SP, and no
PTO detail. A name appears only as the owner of an item.

### 6. Return the result

Return the `sprint_health` dict to the caller (the weekly report). Run on its own
(for example in a DM), reply with
`chai_report.build_report(result, [], left_out, mentions)`: post its
`top_post_text` (the three top lines as one message), then each string of its
`replies` in the thread.

## Missing data

| Missing | Result |
|---------|--------|
| Current sprint | Sprint health left out, with the reason |
| Sprint issue fetch incomplete | Sprint health left out (`sprint issue fetch incomplete: <problem>`) |
| A velocity sprint's fetch incomplete, or no roster | Velocity and capacity "not available"; other figures still reported |
| Current sprint availability | Capacity not adjusted, labelled "not PTO-adjusted" |
| Issue links | Blocked-by links left out and listed |

Never fill a gap from memory or by estimating.
