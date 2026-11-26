---
name: scrum-context
description: Read-only coordinator skill that answers "where are we in the sprint and release cycle?" for other chai-bot skills — current sprint, release window and phase, next ceremonies, and team availability per sprint
---

# Scrum Context (chai-bot coordinator)

This playbook is for the **chai-bot coordinator**, not for Claude Code. It answers
"where are we in the sprint and release cycle?" so other chai-bot skills can reuse
the answer. It is **read-only**: it posts nothing, edits nothing, and returns a
structured result to the calling skill.

## Tools

| Tool | Used for |
|------|----------|
| `list_jira_board_sprints` | Sprints on board 11479 |
| Product Pages (`list_entities` and schedule reading, as the persona does) | OpenShift releases and the milestones of `openshift-<X.Y>` |
| OpenShift Edge Google Calendar | Ceremony events, absences, and holidays |
| `sandbox_exec` (Python, pandas/numpy) | Runs `plugins/edge-scrum/bin/scrum_context.py` from the repo |

## Configuration

```yaml
board_id: 11479
product_pages_release: "openshift-<X.Y>"
calendar: "OpenShift Edge"
ceremonies:            # matched by event title, case-insensitive
  - "Sprint Planning"
  - "Mid-Sprint Feature Readback"
  - "Sprint Demos"
  - "Retrospective"

# The only roster setting. Default: edge-context people/team-roster.md,
# keeping roles containing "Software Engineer" or "Software Quality Engineer",
# at any level (for example Senior or Principal), minus anyone listed under
# "Not counted for sprint capacity".
# Unset or unreadable → availability is "not available (roster not configured)";
# every other part of the result still works.
ROSTER_SOURCE: "openshift-eng/edge-context: people/team-roster.md"
ROSTER_ROLES: ["Software Engineer", "Software Quality Engineer"]  # "contains" match, any level
```

## Modes

| Invocation | Returns |
|------------|---------|
| No argument | Current sprint (name, id, start, end, week 1–3); the release in progress and its phase, among the candidate releases whose schedules the caller passed |
| A release, e.g. `5.1` | Its window: refinement sprint, development sprints, pencils-down sprint, branch-cut sprint, development sprints remaining, and phase |

Both modes also return the **next ceremony dates**, the **next sprint's team
availability**, and the availability of the **current sprint and each later
sprint** up to the latest branch-cut sprint (of the release asked for, or of the
releases in progress).

Phases: `refinement`, `development`, `pencils-down sprint`, `pre-branch-cut`,
`not started`, `finished`, `not scheduled yet`.

The weekly report uses them automatically, never by prompt:

| From scrum-context | Weekly report shows |
|---|---|
| `week` 1 | sprint reply in capacity mode (a one-week sprint too: week 1 is never the final-week view) |
| `week` 2 | sprint reply in mid-sprint mode |
| `week` = the sprint's last week (from its dates), week 2 or later | mid-sprint mode plus the final-week view |
| phase `refinement` (the refinement sprint, all three weeks) | refinement checks for every non-Closed Feature targeting that release (week 1: "no refinement spike" as one count) |
| phase `development`, `pencils-down sprint`, `pre-branch-cut` | release checks (scope vs capacity, schedule, blocks, cut line, staffing, refinement overdue) |
| `not started` / `finished` for every release | no release reply; the Left out line says "no release in progress" |

The refinement sprint, development window and branch-cut sprint come from the
board sprints and the Product Pages milestones (see Rules below); the
"behind schedule" check measures the development window from the sprint after
the refinement sprint through the branch-cut sprint.

## Steps

### 1. Sprints

Call `list_jira_board_sprints` for board **11479** (all states). Keep only the
board's own sprints: drop any sprint marked with another origin board (the board
also lists sprints shared from other teams' boards). Pass the kept sprints as
`sprints`, keeping `id`, `name`, `state`, `start_date`, `end_date`, and the
origin-board marker if present (`originBoardId` or `origin_board`); the helper
also drops marked sprints.

### 2. Product Pages schedule

Only these Product Pages calls are supported, with the Product Pages
`list_entities` tool. Each response has `data` (the entities, a list) and
`cursor` (the next page's cursor, or null on the last page); a response
without either, or with an empty `cursor`, is not readable (cut off). Each
request is sent at most twice: one that fails or is not readable is sent again
once, unchanged; if the second answer also fails or is not readable, the
lookup failed:

1. **OpenShift product:** `list_entities(kind="product", shortname="openshift")`,
   then, while the response's `cursor` is set, the same call with
   `cursor=next_cursor` (the same filters, as for releases below; a cursor
   already seen means the lookup failed). `ocp_product_id` is the integer `id`
   of the one entry whose `shortname` is `openshift`, counted across every page
   read. Never hard-code or guess it; no single match, or no integer `id`, means
   the lookup failed.
2. **Releases:** `list_entities(kind="release", public_parent_id=ocp_product_id)`,
   then, while the response's `cursor` is set,
   `list_entities(kind="release", public_parent_id=ocp_product_id, cursor=next_cursor)`
   with `next_cursor` = that `cursor`, the same filters, and **no** `phase__in`
   or other phase filter (phase values are not verified, and a phase label is
   never used to pick releases). A cursor already seen in this listing means
   the listing failed: do not fetch it again.
3. **Schedules:** read the schedule of each needed `openshift-<X.Y>` release
   from its listed entity, as the persona does.

Make these calls yourself with the Product Pages tool: the sandbox has no
access to it. The same sequence as code (the tests run this block with a
stand-in for the tool):

```python
def openshift_releases(list_entities, why=None):
    """Every OpenShift release entity Product Pages lists, or None when a lookup failed
    (then its reason is appended to ``why``, a list, when given)."""
    def failed(reason):
        if why is not None:
            why.append(reason)
        return None

    def readable(page):
        """data a list, and cursor present: null (the last page) or a non-empty value."""
        return (isinstance(page, dict) and isinstance(page.get("data"), list) and "cursor" in page
                and page["cursor"] != "")

    def call(**request):
        """One page. A failed call or a page not readable (cut off) is sent again once,
        unchanged: each request at most twice. None when the second answer is not
        readable either; a second failure raises (the lookup failed)."""
        try:
            page = list_entities(**request)
        except Exception:
            page = None
        if not readable(page):
            page = list_entities(**request)
        return page if readable(page) else None

    def every_page(**filters):
        """Every entity of a listing, each non-null cursor followed with the same filters;
        None when a page is still not readable after its retry or a cursor repeats."""
        entities, seen, page = [], set(), call(**filters)
        while True:
            if page is None:
                return failed("a listing page still not readable after its retry")
            entities += page["data"]
            next_cursor = page["cursor"]
            if next_cursor is None:
                return entities  # an explicit null cursor: the last page
            if next_cursor in seen:
                return failed("a listing cursor repeated")  # the listing failed; not fetched again
            seen.add(next_cursor)
            page = call(**filters, cursor=next_cursor)

    try:
        found = every_page(kind="product", shortname="openshift")
        if found is None:
            return None  # the product listing was not read whole
        products = [p for p in found if isinstance(p, dict) and p.get("shortname") == "openshift"]
        if len(products) != 1 or type(products[0].get("id")) is not int:
            return failed("no single OpenShift product with an integer id")  # across every page
        return every_page(kind="release", public_parent_id=products[0]["id"])
    except Exception as exc:  # the call failed again after its retry: the lookup failed
        return failed(f"the call failed again after its retry: {' '.join(str(exc).split()) or type(exc).__name__}")
```

Call it with `why = []`. When it returns None, pass every needed release as
`{"error": why[0]}` (below): the release section's omission then names that error.

Which releases:

- **Release argument:** that release and the **previous** one, the highest
  listed release below it (its "Main Branch Open for <X.Y>" is used when the
  release has no "Development Branch opens" of its own).
- **No argument:** the caller's candidate releases, plus the **previous**
  release of each (the highest listed release below it). The weekly report's candidates are the releases its Feature
  gate names (the weekly-report script). With no candidates, pass no
  schedules: `release_in_progress` is then "not available".

Pass each release's result in `schedules`:

| Product Pages result | Pass | Window phase |
|----------------------|------|--------------|
| Schedule read, with milestones | the milestone list | from the milestones (below) |
| Lookup succeeded, but no dates (or the release is not listed) | `[]` | "not scheduled yet", unless the previous release's "Main Branch Open for <X.Y>" places it |
| Product lookup, listing or schedule lookup failed | `{"error": "<reason>"}` | "release phase unknown: Product Pages lookup failed" |

A release that needs a failed previous release's "Main Branch Open for
<X.Y>" (it has no "Development Branch opens" of its own) is also "release
phase unknown: Product Pages lookup failed". Never guess dates for it.

Pass milestones per release as `{"name": ..., "date": "YYYY-MM-DD"}` (or
`start`/`end` for ranges). Pass them as written; the helper matches by name
prefix, ignoring a leading version and case:

| Anchor | Milestone name prefix | Sprint mapping |
|--------|-----------------------|----------------|
| Refinement sprint | `<X.Y> Development Branch opens` | Sprint starting that day, or the first sprint starting within the next 7 days |
| Pencils-down sprint | `Pencils Down…` (e.g. "Pencils Down / Stabilization", "… Stabilization Gate") | Last sprint ending on or before it |
| Branch-cut sprint | `Main Branch Open for <next>` (older schedules: `Master Branch Open for <next>`) | Sprint containing it |

For the pencils-down and branch-cut sprints, a date exactly on a sprint boundary
belongs to the sprint **ending** that day. The refinement sprint is the exception:
it is matched by its **start** day, so a "Development Branch opens" date on a
boundary maps to the sprint starting that day. The boundary rule does not apply
to today either (see the rules below).
Other milestones (for example "Post-Branching / Feature Freeze") are not needed
and may be missing. If a release has no schedule at all, its window is
"not scheduled yet".

### 3. Calendar

Read OpenShift Edge calendar events in a window that does not depend on the
board: from 21 days before today through 42 days after today. If a ceremony has
no non-cancelled occurrence on or after today in that window, extend the end in
21-day steps, up to 126 days after today. Pass every event in the final window
whose title matches a ceremony.

- **Ceremonies:** for each event whose title contains one of the configured
  ceremony titles:
  1. **Outside the sandbox**, check the raw title for a cancelled prefix
     ("Cancelled:", "Canceled:", "CANCELLED", case-insensitive). If found, set
     `status: "cancelled"` on that event.
  2. Pass `{"ceremony": "<matched configured label>", "start", "status"}`.
     `ceremony` is the configured label exactly (for example `"Retrospective"`),
     **never the raw title**, which can carry names or emails.

  Cancelled events stay in the list with `status: "cancelled"`; the helper
  ignores them, so a cancelled ceremony comes back as `null`. The helper relies
  on `status` for cancellation, because it never sees the title. (It still
  accepts a legacy `title` field and checks that for the prefix.)
- **Absences and holidays:** read absences from 21 days before today through
  126 days after today, and pass that window as `absence_window`. Read
  `ROSTER_SOURCE` and keep only engineers whose role contains "Software
  Engineer" or "Software Quality Engineer", at any level. Then drop anyone listed
  under "Not counted for sprint capacity" in the roster (people who don't take
  sprint work) **before counting engineers**. `chai_jira_adapter.parse_team_roster`
  applies both rules if you run it on the roster file alone (never with calendar
  data). Map each roster engineer to an anonymized id (`p1`, `p2`, …) **outside**
  the sandbox. For each absence entry that belongs to a roster engineer, pass
  `{"person_id", "start", "end"}` (inclusive dates). For a **team-wide or regional
  holiday** that names no single person, add one entry per affected roster
  engineer (every engineer for a team-wide holiday; the engineers in that
  country or region for a regional one). Drop entries for anyone not on the
  roster. Pass `engineer_count` = number of roster engineers.
- **Never pass names, emails, event titles, or event descriptions into the
  sandbox or the output.** Only ceremony labels, anonymized ids, and dates.

If the roster is not configured or not readable, omit `engineer_count`.

### 4. Run the helper

In `sandbox_exec`, load `plugins/edge-scrum/bin/scrum_context.py` from the repo
and call it either way:

```python
import scrum_context
result = scrum_context.build_context(data)
```

```bash
python3 scrum_context.py --input context_in.json --output context_out.json
```

Input:

Send `release` as a string, and only when it matches `^\d+\.\d+$` (for example `"5.1"`); omit it
when no release was asked for. If the caller passes anything else, return
"not available (invalid release; expected X.Y)" without calling the helper. The
helper returns the same value if it still receives one, and ignores schedule
keys that are not `X.Y`.

```json
{
  "today": "YYYY-MM-DD",
  "board_id": 11479,
  "release": "X.Y or omitted",
  "sprints": [{"id": "...", "name": "...", "state": "...", "start_date": "...", "end_date": "...", "originBoardId": 11479}],
  "schedules": {"X.Y": [{"name": "...", "date": "YYYY-MM-DD"}]},
  "events": [{"ceremony": "Sprint Planning", "start": "YYYY-MM-DD", "status": "confirmed"}],
  "absences": [{"person_id": "p1", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}],
  "absence_window": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "engineer_count": 0
}
```

Omit a key when its source was not available. The helper then returns
"not available" for that part instead of guessing. The exception is Product
Pages: pass each release whose lookup failed as `{"error": "<reason>"}` in
`schedules`, never omit it, so its phase says the lookup failed.

### 5. Return the result

Return the helper output as is. Its keys are `current_sprint` (with `week`),
`next_sprint`, `release_in_progress` (no argument) or `release` (with an
argument), `next_ceremonies`, `current_sprint_ceremonies`,
`next_sprint_availability`, `current_sprint_availability`,
`sprint_availability` (one entry per sprint from the current sprint through the
latest branch-cut sprint, or the next sprint when no branch cut is known; each
with `name`, `number`, `start`, `end`, `projected`, `availability`), and
`warnings`.

`warnings` lists source items skipped for a malformed date, for example
"skipped sprint 'Sprint 12': bad end date". It never contains the raw value or
an event title. A malformed `today` is an error, not a warning.

## Rules the helper applies

- **Today's sprint:** the board sprint with state `active`. On the active
  sprint's end day, a board sprint that starts that day is today's sprint, even
  if nobody has started it yet (the sprint-change Monday: week 1, and its
  release phase). Only when no sprint
  is active, the sprint whose start day is on or before today and whose end day
  is after today, so a sprint's first day belongs to the new sprint. A date past
  the last board sprint maps to a projected sprint (up to the 12-sprint limit
  below). Phase and development sprints
  remaining are measured from today's sprint.
- **Development sprints:** the sprints after the refinement sprint and before the
  pencils-down sprint. **Development sprints remaining** counts the current and
  later development sprints plus the pencils-down sprint; it is 0 after pencils
  down.
- **Phase:** `not started` before the refinement sprint; `refinement`,
  `development`, or `pencils-down sprint` by today's sprint;
  `pre-branch-cut` after the pencils-down sprint until the day before
  "Main Branch Open"; `finished` from that date on.
- **Ceremonies in a sprint:** Sprint Demos and Retrospective are looked up from
  the day after the sprint starts through its end day; Sprint Planning and the
  readback from its start day through the day before its end. A neighbouring
  sprint's ceremony on the shared boundary day is never borrowed.
- **Projected sprints:** when a milestone or today falls after the last sprint
  on the board, the helper extends the board's cadence and marks those sprints
  `"projected": true` with no id. Say "projected" when reporting them. It
  projects at most **12 sprints** (about 36 weeks) past the last board sprint.
  Beyond that, the current sprint is "not available" and the milestones
  "not scheduled yet"; that usually means the board is stale.
- **Availability:** `1 − person-days off ÷ person-days` over a sprint's
  working days (Mon–Fri, start day through the day before the end day), rounded to
  2 decimals. Overlapping entries count once per person-day. Team-level only. In
  `sprint_availability`, a sprint whose working days are not all inside
  `absence_window` is "not available (outside the calendar window read)",
  since later absences were never seen. Without `absence_window`, the window is
  21 days before through 42 days after today.

## Missing sources

| Missing | Result |
|---------|--------|
| Board sprints | `current_sprint`, `next_sprint`: "not available" |
| Product Pages has no schedule for a release (lookup succeeded) | That release: "not scheduled yet" |
| Product Pages lookup failed for a release | That release: "release phase unknown: Product Pages lookup failed" |
| One milestone | That anchor (and anything derived from it): "not scheduled yet" |
| Calendar | Ceremonies: "not available" |
| Roster | Availability: "not available (roster not configured)" |
| A malformed date on a sprint, milestone, event, or absence | That item is skipped and listed in `warnings` |

Never fill a gap from memory or by estimating dates.
