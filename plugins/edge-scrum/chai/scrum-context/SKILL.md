---
name: scrum-context
description: Read-only coordinator skill that answers "where are we in the sprint and release cycle?" for other chai-bot skills — current sprint, release window and phase, next ceremonies, and next sprint's team availability
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
| Product Pages schedule reading (as the persona does) | Milestones for `openshift-<X.Y>` |
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
# at any level (for example Senior or Principal).
# Unset or unreadable → availability is "not available (roster not configured)";
# every other part of the result still works.
ROSTER_SOURCE: "openshift-eng/edge-context: people/team-roster.md"
ROSTER_ROLES: ["Software Engineer", "Software Quality Engineer"]  # "contains" match, any level
```

## Modes

| Invocation | Returns |
|------------|---------|
| No argument | Current sprint (name, id, start, end, week 1–3); the release in progress and its phase |
| A release, e.g. `5.1` | Its window: refinement sprint, development sprints, pencils-down sprint, branch-cut sprint, development sprints remaining, and phase |

Both modes also return the **next ceremony dates** and the **next sprint's team
availability**.

Phases: `refinement`, `development`, `pencils-down sprint`, `pre-branch-cut`,
`not started`, `finished`, `not scheduled yet`.

## Steps

### 1. Sprints

Call `list_jira_board_sprints` for board **11479** (all states). Keep only the
board's own sprints: drop any sprint marked with another origin board (the board
also lists sprints shared from other teams' boards). Pass the kept sprints as
`sprints`, keeping `id`, `name`, `state`, `start_date`, `end_date`, and the
origin-board marker if present (`originBoardId` or `origin_board`); the helper
also drops marked sprints.

### 2. Product Pages schedule

Read the schedule for `openshift-<X.Y>`:

- **No argument:** read the schedule of **every OpenShift release Product Pages
  lists as Planning or in development**, plus the **previous** release (the most
  recent one before them). Pass a release with no schedule yet as an empty list;
  the helper still reports it as in progress when its refinement sprint can be
  inferred from the previous release's "Master Branch Open for <X.Y>".
- **Release argument:** read that release and the **previous** one (its
  "Master Branch Open for <X.Y>" is used when the release has no
  "Development Branch opens" of its own).

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
- **Absences and holidays:** read `ROSTER_SOURCE` and keep only engineers whose
  role contains "Software Engineer" or "Software Quality Engineer", at any
  level. Map each roster engineer to an anonymized id (`p1`, `p2`, …) **outside**
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
  "engineer_count": 0
}
```

Omit a key when its source was not available. The helper then returns
"not available" for that part instead of guessing.

### 5. Return the result

Return the helper output as is. Its keys are `current_sprint` (with `week`),
`next_sprint`, `release_in_progress` (no argument) or `release` (with an
argument), `next_ceremonies`, `current_sprint_ceremonies`,
`next_sprint_availability`, and `warnings`.

`warnings` lists source items skipped for a malformed date, for example
"skipped sprint 'Sprint 12': bad end date". It never contains the raw value or
an event title. A malformed `today` is an error, not a warning.

## Rules the helper applies

- **Today's sprint:** the board sprint with state `active`. Only when no sprint
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
- **Availability:** `1 − person-days off ÷ person-days` over the next sprint's
  working days (Mon–Fri, start day through the day before the end day), rounded to
  2 decimals. Overlapping entries count once per person-day. Team-level only.

## Missing sources

| Missing | Result |
|---------|--------|
| Board sprints | `current_sprint`, `next_sprint`: "not available" |
| Product Pages schedule for a release | That release: "not scheduled yet" |
| One milestone | That anchor (and anything derived from it): "not scheduled yet" |
| Calendar | Ceremonies: "not available" |
| Roster | Availability: "not available (roster not configured)" |
| A malformed date on a sprint, milestone, event, or absence | That item is skipped and listed in `warnings` |

Never fill a gap from memory or by estimating dates.
