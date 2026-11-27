---
name: sprint-health
description: Read-only coordinator skill that reports team-level sprint health for the current sprint — capacity mode in sprint week 1, mid-sprint mode after, with a final-week view in the last week — with no prompts and no per-person sections
---

# Sprint Health (chai-bot coordinator)

> **Deprecated.** Replaced by [`weekly-report`](../weekly-report/SKILL.md), which runs the report as a script (`bin/weekly_report_files.py`). Kept only while the live persona still registers this playbook; it is removed once ship-help-bot#1415 (OCPEDGE-3276) is deployed.

This playbook is for the **chai-bot coordinator**, not for Claude Code. It reports
the health of the current OpenShift Edge sprint at **team level**, as the
sprint-health part of the weekly health report (edge-context decision 0018). It is
**read-only**: it edits nothing in Jira and asks no questions. The analysis runs
inline in the coordinator; there are no sub-agents.

## Tools

| Tool | Used for |
|------|----------|
| `scrum-context` skill | Current sprint, its week, and its availability |
| `list_jira_board_sprints` | Board 11479's sprints: scrum-context's one listing (the velocity sprints) |
| `query_jira` | Sprint issues (paged; see Jira paging) |
| `get_jira_issue` | Issue links of blocked candidates (mid-sprint mode) |
| `sandbox_exec` (Python) | `plugins/edge-scrum/bin/` scripts on the fetched data |

Jira credentials are not available in the sandbox: fetch everything with the
tools above, then pass the text into `sandbox_exec`.

**Run by the weekly report**, this skill uses the weekly report's shared
fetches (sprint issues, blocked candidates, their links, the velocity sprints
and the optional velocity store: weekly steps 2b and 3) in place of its own.

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

> `Showing the first 50 matching issues; more match:` is normal. It is never an error, never partial data and never a reason to stop: call `next_page_jql` and fetch that page now, until it returns `None`.

`query_jira` returns text with no total count. Fetch every query this way, with
`max_results: 50` on every call:

1. First page: `chai_jira_adapter.page_jql(<JQL>)`, that is
   `(<JQL>) ORDER BY key DESC`.
2. Send the pages of every fetch that is ready together (a **paging round**),
   then decide all of them in **one** sandbox call (the sandbox cannot call
   Jira; never one call per page):
   `decisions = chai_jira_adapter.paging_round({slot: {"jql": sent, "page": page_text, "retried": <bool>}})`, `slot` unique per fetch part (a split's parts go out together: `sprint_issues/TESTA`, `sprint_issues/TESTB`; the fetch's name stays for `adapt_fetches`),
   with `sent` the exact query sent for that page, `page_text` its answer (a failed call: `{"error": "<message>"}`)
   and `retried` true only on that same query's second send. It runs `next_page_jql` on each page:
   `{"next": <query>}` is the next page (send it in the next round),
   `{"done": True}` means that page was the last one, and step 3 says what
   `{"retry": ...}`, `{"incomplete": ...}` and `{"split": [...]}` mean. Keep each
   page with its query: `{"text": page_text, "jql": sent}` (a failed call:
   `{"error": "<message>", "jql": sent}`); a page kept by an `incomplete` decision also keeps `"incomplete": <its reason>`.
3. `next_page_jql` raises only for these, never for a whole page that says more
   match (`paging_round` returns them as `retry` or `incomplete`, and `split`):
   - `PageNotWhole`: the call failed, the page is cut off or not readable, it lists an issue twice or one outside the project restriction paging added, a last page has a key not below its `key <` or of another project, or a page under a valid `key <` has none of that project's keys.
     Send the same query again once; if it raises again, keep the retry's
     answer (with that reason, step 2) and stop: the fetch is incomplete, with that reason.
   - `PageDidNotAdvance` (a `PageNotWhole`): the tool ignored `key <` (a key not below it). Send the same
     query again once; if it does not advance again, keep that answer and stop: incomplete, with that reason.
   - `QueryError`: the query's own `key <` is of another project; sending it again cannot help: stop, incomplete.
   - `SplitByProject` (not a failure): Jira's `key <` compares keys within one
     project, so a query over several projects goes on per project. Fetch each
     part in `split.parts` (from `paging_round`: `decisions[name]["split"]`)
     from its first page (`part["jql"]`) with steps 2–3,
     keep its pages as a manifest part with `"project": part["project"]`, and
     drop the pages of the query that raised it (the parts fetch them again).
     `part["project"]` is `None` for the part that covers every other project:
     fetch it like the others. A part can raise `SplitByProject` again: drop
     its pages too; its parts take its place. A query whose `project =` or
     `project in (...)` clause is joined to the rest by AND can also be split
     before its first call: start with `page_jql(<JQL>, project=<P>)` for each
     project that clause names.
4. When every part of a fetch has ended (`done`, or stopped by step 3 with its last page or failed slot kept),
   adapt it **once**, every page and part in order, in that round's sandbox call (the kept page gives the reason):
   `chai_jira_adapter.adapt_fetches({name: {"parts": [...]}}, roster)` (or
   `adapt_fetch` for one): it rechecks headers, page sizes and key order and
   returns `fetch.complete`. Process a fetch only after that, never page by
   page. Every page uses `max_results: 50` and `extra_fields` =
   `chai_jira_adapter.EXTRA_FIELDS[<fetch>]` only (base fields such as summary,
   type, status, assignee, labels and parent are always printed). A fetch with
   several pages or parts is complete when each part ended at `None` and
   `adapt_fetch` gives `fetch.complete`. It is
   incomplete only as step 3 or steps 5–7 say: then compute nothing from it;
   list it as left out, with the reason.
5. A "Create metadata …" block may come before the results. If it says
   `Invalid extra_fields entries: …`, those fields were not returned: the fetch
   counts as incomplete, except for Flagged (`customfield_10021`), which is then
   reported as an unavailable signal. Unset custom fields print no line, so a
   missing Flagged line means "not flagged".
6. Output may be wrapped as `{"result": "<text>"}`; the adapter unwraps it. A
   query with no results returns `No issues found for JQL: <jql>`: pass the JQL
   sent with each page as `"jql"` in the manifest page
   (`{"text": ..., "jql": "<query sent>"}`). The page counts as complete and
   empty only when that JQL matches the query sent; otherwise
   `next_page_jql` raises `PageNotWhole` (step 3).
7. When `query_jira` says a field value was shortened or stored, read it whole
   with one call, retried once if it fails (`result_page` for a stored result,
   or the `get_jira_issue` call the note names), and put the whole value in
   place of the shortened one in that page's text before passing it to the
   script. If it cannot be read whole, pass the page as it is: the adapter
   marks the fetch incomplete (for example `<KEY>: <field> value shortened`),
   and it is left out like any incomplete fetch, never guessed.
8. Labels `query_jira` prints that the adapter doesn't know are ignored with a
   warning; they never change a known field. A date that isn't readable is
   dropped, never guessed, and listed in the adapted fetch's `left_out`: add
   every fetch's `left_out` to the report's `missing` so it appears under
   "Left out".

**Example** (synthetic projects `TESTA` and `TESTB`):

1. Send `(project in (TESTA, TESTB) AND sprint = 1001) ORDER BY key DESC`. The
   answer starts `Showing the first 50 matching issues; more match:` and lists
   issues of both projects: `next_page_jql` raises `SplitByProject` with one
   part per project.
2. `TESTA`: send
   `(project in (TESTA, TESTB) AND sprint = 1001) AND project = TESTA ORDER BY key DESC`.
   It shows the first 50, last issue `TESTA-131`: send
   `(project in (TESTA, TESTB) AND sprint = 1001) AND project = TESTA AND key < TESTA-131 ORDER BY key DESC`.
   That page starts `Found 30 issues (showing 30):`: `None`.
3. `TESTB`: send
   `(project in (TESTA, TESTB) AND sprint = 1001) AND project = TESTB ORDER BY key DESC`.
   It starts `Found 12 issues (showing 12):`: `None`.
4. Pass the parts `TESTA` (2 pages) and `TESTB` (1 page) to `adapt_fetch`, not
   the first page: complete, 92 issues.

```python
import chai_jira_adapter as ad
adapted = ad.adapt_fetch({
    "name": "sprint_issues",
    "parts": [{"project": "OCPEDGE", "pages": [{"text": page_1, "jql": "<the JQL sent>"},
                                               {"text": page_2, "jql": "<the JQL sent>"}]},
              {"project": "USHIFT", "pages": [{"text": page_1, "jql": "<the JQL sent>"}]}]},
    # or "pages": [...] for one part; "jql" is the exact query sent for that page (step 6)
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
  **mid-sprint**. Week 1 stays capacity even when it is the sprint's last week (a
  one-week sprint): it never gets the final-week view. No prompt. A missing or
  non-numeric week gives no mode: run neither analysis; sprint health is left out
  ("sprint week unknown").
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
- **extra_fields:** `customfield_10028, customfield_10014, customfield_10470`
  (`chai_jira_adapter.EXTRA_FIELDS["sprint_issues"]`; summary, status, type,
  priority, assignee, dates, labels and parent are base fields)

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
blocked signal is left out, not guessed. Pass `links_problems`: every problem
`attach_issue_links` returns, and `blocked items fetch incomplete: <the fetch's
problems>` when the candidate fetch is incomplete, so the Left out line names
the failed fetch and its error.

### 4. Velocity (capacity mode)

Velocity is `compute-velocity.py`: the plain average of completed, team-owned SP
over the **last 3 closed sprints** (decision 0015). The current sprint is never
included.

1. The three closed sprints of the board with the highest number below the
   current sprint, from scrum-context's board listing (all states, read once;
   no second listing): `closed = scrum_context.last_closed_sprints(listing,
   11479, <current sprint number>)`. It keeps only the board's own sprints
   (a sprint marked with another origin board is skipped).
2. For each (with the weekly report's velocity store: only those
   `velocity_cache.plan` returns in `to_fetch`), `query_jira`, paged:
   `project in (OCPEDGE, USHIFT, OCPBUGS) AND sprint = <id>`, `extra_fields`
   `resolutiondate, customfield_10028, customfield_10470, customfield_10020`
   (`EXTRA_FIELDS["velocity"]`). Adapt with the roster, then
   `transform-sprint-issues.py` with that sprint's metadata.
3. `compute-velocity.py --sprint-issues <the fetched files> --roster roster.json`;
   with stored entries, `velocity_json = velocity_cache.combine(closed, hits,
   <its output>)` (compute-velocity's figures, the stored sprints from their
   aggregates).
4. `team_velocity, reason = chai_report.usable_velocity(velocity_json)`.

If any of the sprint fetches is incomplete, or `usable_velocity` returns a reason
(fewer than 3 sprints, or done team issues skipped for missing sprint and
resolution data, which would understate it), velocity is **not available**
(left out with the reason); never average fewer sprints or estimate.

Capacity: `chai_report.sprint_capacity(team_velocity, current_sprint_availability)`
= velocity × the current sprint's availability. When availability is not a
number, capacity is the velocity unadjusted, labelled **"not PTO-adjusted"**.

### 5. Analysis (inline)

Run `chai_report.sprint_health(sprint_issues, current_sprint, today, mode,
velocity, availability, skipped_signals, links_complete,
velocity_reason=velocity_reason, links_problems=links_problems)`, where
`velocity_reason` is the reason `usable_velocity` returned (None when velocity
is usable); it becomes the reason capacity is left out. The checks themselves are `_scrum_rules.sprint_findings`:

- **Capacity mode (week 1):** committed SP and issues, capacity, committed vs
  capacity, and per issue "unpointed" and "no epic" (owner: the assignee). Over
  capacity is a 🔴 risk and a team item for the Scrum Master ("re-plan the
  sprint"); unpointed and epic-less issues are 🟡.
- **Mid-sprint mode (week 2 on):** burndown (expected = total SP × elapsed ÷
  total days; On Track / Slightly Behind (≤ 10% of total) / Behind; Behind adds
  "re-scope the sprint" for the Scrum Master), projected finish, and **explicit
  blocks only** on open issues: "blocked by KEY" (an open "is blocked by" link)
  and the "Blocked label" / "Parked" labels. There is no stale (no recent
  update) check.
- **Final-week view (the sprint's last week, from its dates: `chai_report.final_week`;
  in mid-sprint mode, so week 2 or later):**
  the header and top post say "final week" in place of "week N, mid-sprint"; done vs expected and explicit blocks stay;
  "N still open (X SP): A in review or testing (B SP), C in progress or to do (D SP)"
  (law 07 statuses: Review / MODIFIED / ON_QA, and To Do / In Progress / NEW / ASSIGNED; any
  other open status as "E other status (F SP)"). No carry-over items, no projected
  finish and no "re-scope the sprint" line (earlier weeks keep them).

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
| Sprint issue fetch incomplete (Jira paging step 3 or 5–7; never because it had several pages) | Sprint health left out (`sprint issue fetch incomplete: <problem>`) |
| A velocity sprint's fetch incomplete, or no roster | Velocity and capacity "not available"; other figures still reported |
| Current sprint availability | Capacity not adjusted, labelled "not PTO-adjusted" |
| Issue links | Blocked-by links left out and listed |

Never fill a gap from memory or by estimating.
