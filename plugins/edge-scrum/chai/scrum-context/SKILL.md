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
| `week` 1 | sprint reply in capacity mode |
| `week` 2 | sprint reply in mid-sprint mode |
| `week` 3 (the sprint's last week) | mid-sprint mode plus the sprint-end view |
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

Only these Product Pages calls are supported:

1. **OpenShift product:** resolve `ocp_product_id` with the Product Pages
   product lookup (the OpenShift Container Platform product entity). Never
   hard-code or guess it.
2. **Releases:** `list_entities(kind="release", public_parent_id=ocp_product_id)`,
   with **no** `phase__in` or other phase filter (the phase values are not
   verified, and a phase label is never used to pick releases). The response
   has `data` and `cursor`: while it returns a cursor, call again with that
   cursor; stop when there is none. A cursor already seen in this listing, or
   a call that fails, means the listing failed (do not retry it).
3. **Schedules:** read the schedule of each needed `openshift-<X.Y>` release
   from its listed entity, as the persona does.

Which releases:

- **Release argument:** that release and the **previous** one, the highest
  listed release below it (its "Master Branch Open for <X.Y>" is used when the
  release has no "Development Branch opens" of its own).
- **No argument:** the caller's candidate releases, plus the **previous**
  release of each (the highest listed release below it). The weekly report's candidates are the releases its Feature
  gate names (weekly-health-report step 1). With no candidates, pass no
  schedules: `release_in_progress` is then "not available".

Pass each release's result in `schedules`:

| Product Pages result | Pass | Window phase |
|----------------------|------|--------------|
| Schedule read, with milestones | the milestone list | from the milestones (below) |
| Lookup succeeded, but no dates (or the release is not listed) | `[]` | "not scheduled yet", unless the previous release's "Master Branch Open for <X.Y>" places it |
| Product lookup, listing or schedule lookup failed | `{"error": "<reason>"}` | "release phase unknown: Product Pages lookup failed" |

A release that needs a failed previous release's "Master Branch Open for
<X.Y>" (it has no "Development Branch opens" of its own) is also "release
phase unknown: Product Pages lookup failed". Never guess dates for it.

Pass milestones per release as `{"name": ..., "date": "YYYY-MM-DD"}` (or
`start`/`end` for ranges). Pass them as written; the helper matches by name
prefix, ignoring a leading version and case:

| Anchor | Milestone name prefix | Sprint mapping |
|--------|-----------------------|----------------|
| Refinement sprint | `<X.Y> Development Branch opens` | Sprint starting that day, or the first sprint starting within the next 7 days |
| Pencils-down sprint | `Pencils Down…` (e.g. "Pencils Down / Stabilization", "… Stabilization Gate") | Last sprint ending on or before it |
| Branch-cut sprint | `Master Branch Open for <next>` | Sprint containing it |

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
  "Master Branch Open"; `finished` from that date on.
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
