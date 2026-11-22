---
name: weekly-health-report
description: Read-only coordinator skill that posts the weekly OpenShift Edge sprint and release health report (edge-context decision 0018) — a 3-line top post and at most 3 thread replies with owner lines, by sprint week and release phase
---

# Weekly Health Report (chai-bot coordinator)

This playbook is for the **chai-bot coordinator**, not for Claude Code. It
implements edge-context decision 0018 end to end: sprint health for the current
sprint plus one release section per release in progress, posted as one message
with details in its thread. It is **read-only** in Jira, asks no questions, and
uses no sub-agents.

## Tools

| Tool | Used for |
|------|----------|
| `scrum-context` skill | Current sprint and week, releases in progress, phases, availability |
| `sprint-health` skill | Sprint section (capacity or mid-sprint mode) |
| `release-health` skill | One release section per release in progress; the epic Target Version and QA Contact flags (once per report) |
| Slack user lookup by email (as the persona does) | Owner mentions |
| Slack post | The top post and its thread replies |
| `sandbox_exec` (Python) | `plugins/edge-scrum/bin/chai_report.py` and the scripts the skills use |

All Jira reads follow the **Jira paging** recipe below, through the skills.

## Configuration

```yaml
schedule: "Mondays 10:00 UTC"            # set on the chai-bot task, not here
channel: "#team-ocp-edge-notifications"
ROSTER_SOURCE: "openshift-eng/edge-context: people/team-roster.md"
```

## Jira paging

`query_jira` returns text with no total count. Every fetch in this report:

1. First page: `(<JQL>) ORDER BY key DESC` with `max_results: 50`. The one
   exception is release-health's feature fetch: one page ordered by Rank, for
   the cut line, never paged on with `key <`; if more match, the features are
   fetched again from this step (release-health "Features keep PM rank").
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
   project separately by adding `AND project = <P>`.
5. The fetch is complete only if every page succeeded
   (`chai_jira_adapter.adapt_fetch` → `fetch.complete`). If a call failed or a
   page was cut off, the fetch is **incomplete**: compute nothing from it, post
   what is complete, and list what was left out and why.
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

## Steps

### 1. Where are we

Run **scrum-context** with no argument: `current_sprint` (with `week`),
`current_sprint_availability`, `release_in_progress`.

Then run **scrum-context** once per release with that release as the argument,
for every release in `release_in_progress` **and** every release Product Pages
lists as Planning or in development. Collect each result's `release` value
(the window, not the whole result) as `windows`:
`windows = [result["release"] for result in release_results]`.

### 2. Choose the sections

In the sandbox: `sections, missing = chai_report.select_sections(context, windows)`.

| Release phase (scrum-context) | Release reply: feature checks (`_scrum_rules`) |
|---------------|---------|
| `refinement` (the refinement sprint, every week) | every non-Closed Feature (not Initiative) targeting the release: no SME (alone, on the leads' line), no QA Contact, no Doc Contact, epic with no owner, unsized, no epics, empty epic, no refinement spike (week 1: one count line), spike not in the refinement sprint, test plan spike missing (QE Lead's line) |
| `development` | scope vs capacity; per Feature: no SME (alone), refinement overdue, behind schedule, blocked, epic blocked, Blocked / Parked label, below the cut line, no QA Contact, no Doc Contact, epic with no owner |
| `pencils-down sprint` | the same, flagged as the pencils-down sprint |
| `pre-branch-cut` | the same (work can still land before branch cut) |
| `not scheduled yet`, an anchor not placeable on a sprint, or phase unknown | listed in `missing`, with the reason |
| `not started`, `finished` | no release reply |

Two releases in progress share the one release reply (one header part each).
No release in progress → no release reply, and the Left out line says "no
release in progress"; sprint health is still reported.

| Sprint week | Sprint reply |
|---|---|
| 1 | capacity: sprint SP vs capacity; unpointed, no epic |
| 2 | mid-sprint: burndown; explicit blocks (blocked by KEY, Blocked label, Parked) |
| 3 (last) | the same, plus the sprint-end view: delivered SP and %, likely carry-over (open issues with owners) |

### 3. Sprint health

Run **sprint-health** (week 1 → capacity mode, later → mid-sprint mode; no
prompt). Compute velocity once (`compute-velocity.py`, last 3 closed sprints),
check it with `chai_report.usable_velocity`, and reuse the value **and its
reason** for every release section (`velocity_reason=` in `sprint_health` and
`release_health`), so the Left out line says why capacity is missing. In capacity mode, the current sprint's
capacity = velocity × `current_sprint_availability`; when that is not known,
capacity is not adjusted and is labelled "not PTO-adjusted". Mid-sprint mode
applies no availability adjustment.

### 4. Release sections

For each selected section, run **release-health** for that release (it uses the
window and phase already read). Collect the **computed** results
(`release_health` or `refinement_gaps` output) in a new list, `computed`; add
any section it returns as missing to `missing`. `select_sections` only chooses
sections: never pass its descriptors to `build_report`.

Then run release-health's **epic Target Version and QA Contact** step (step 5,
decision 0024) **once** for the report, whatever the number of release
sections: `gaps = chai_report.epic_field_gaps(epics, parent_batches,
previous_flagged)`. `previous_flagged` is last week's `gaps["flagged_keys"]`
from persona memory, or `None` when memory doesn't have it (then no epic is
marked 🆕). If its fetches fail, the result lists what was left out and why;
every other section is posted as usual.

### 5. Owner mentions

Each item names its owner: the SME (feature-level items; never the feature's
assignee), the QE Lead (test plan spikes), or the assignee (sprint issues and
epics). Features with no SME are one line for the leads, tagging the Scrum
Master; epics or issues with no assignee go to the Scrum Master, as do
team-level items
(re-plan or re-scope the sprint) belong to the **Scrum Master**:
`chai_jira_adapter.find_scrum_master(team_roster_md, scrum_master_md)` reads it
from a roster row whose role contains "Scrum Master", or a
`Current Scrum Master: <name>` line in edge-context `roles/scrum-master.md`.
When neither names one, those items keep "no owner set". Collect the owners to
look up in the sandbox. Team-level items carry no owner until `build_report`
gives them the Scrum Master, and the same goes for epics with no assignee, so
add the Scrum Master yourself. `gaps["epics"]` is the list of flagged epics
(one entry per epic):

```python
parts = ([sprint] if sprint else []) + computed
owners = {i["owner"] for part in parts for i in part["items"] if i.get("owner")}
owners |= {epic["owner"] for epic in gaps["epics"] if epic["owner"]}
if scrum_master:
    owners.add(scrum_master)
```

An empty epic result adds no owner; an owner of several epics counts once. For
each owner:

1. Find the owner's row in `parse_team_roster(...)["contacts"]` by
   `display_name`, matched case- and space-insensitively.
2. The row's `email` is `<Rover ID>@redhat.com`, taken from its Rover profile
   link (None when the row has no link).
3. Look up the Slack user by that email; the mention is `<@USER_ID>`.
4. No roster row, no email, or no Slack user → the name, without a mention.

Pass the result as `mentions = {display name: "<@USER_ID>"}`. Whether a mention
pings follows the platform's **Allow Mentions** setting for that person; the
report does not decide it. Do this lookup outside the sandbox.

### 6. Build and post

```python
report = chai_report.build_report(sprint, computed, missing, mentions, team_owner=scrum_master,
                                  epic_gaps=gaps)
top_post_text = report["top_post_text"]  # one string: the channel message
replies = report["replies"]  # strings, one per thread reply, in order
```

`report["top_post"]` is the list of the top post's lines, for checks; post
`top_post_text`, never the list.

- **Top post** = `top_post_text`, exactly three lines: status, sprint,
  week, mode and release phase; the biggest risk (or "No risks this week" with
  the sprint figures); "N people have items: @A, @B, …" (everyone tagged in
  any reply, epic fields included; at most 8 names, then "+N more").
- **Thread replies** = `replies`, **at most 3**, in this order:
  1. sprint health (with the sprint-end view in week 3);
  2. release or refinement health for the current phase (omitted when no
     release is in progress);
  3. epic fields (informational: never changes the colour or the risk line), ending with
     **one** "Left out:" line (missing data with reasons, and the keys of any
     owners over the cap). With no epic flagged, this reply appears only to
     carry that line.

  There is no separate action-items or left-out reply. That leaves room for
  one more reply, Ceremony insights' sprint-start summary on Mondays (decision
  0019): the thread never exceeds 4 replies.

  **Noise budget** (checked by tests): each reply is one header line (with
  counts and "✓ N ready / on track", never a list of what is fine) and at most
  10 owner lines (special lines such as "Leads, assign SMEs" and the week-1
  refinement-spike count use the same slots), `• @Owner: KEY (label, label),
  KEY (label)`: full keys, no summaries; a feature with several problems is one
  entry with several labels, and a label several of the owner's keys share is
  written once: "label for KEY, 4055, 4057 (+2)" (later keys of the same
  project shortened to their number; segments then separated by "; ").
  An owner line is at most 200 characters; what does not fit ends as "+N more
  problem(s)". Nothing is repeated across replies. The whole report (top post
  and replies) stays within 40 lines in the worst case.

  After posting, store `gaps["flagged_keys"]` in persona memory if the persona
  keeps memory, for next week's 🆕 markers, **only when `gaps["complete"]` is
  true and this is the scheduled channel report**: a failed or partial epic
  run, or a run from a DM, must not replace last week's set.

Post `top_post_text` to the channel, then each string of `replies` in its
thread. Run from a DM (validation or an extra run requested by a task manager),
reply in the DM instead of the channel.

## Rules

- **Team level only.** No per-person load, SP or PTO. A name appears only as the
  owner of items on an owner line.
- **Never estimate.** If a source is missing or a fetch is incomplete, post what is
  complete and list what was left out and why (for example
  "9.2 release section: no Product Pages schedule yet"). No figure is computed
  from an incomplete fetch.
- **Nothing to flag** → the top post says "No risks this week" with the sprint
  figures, and "0 people have items". Epic flags never change the colour or the
  risk line; their owners count on the third line.
- **Explicit blocks only.** Blocked means an open "is blocked by" link or a
  Blocked / Parked label; there is no stale (no recent update) check.
- **Read-only** in Jira. Release testing (EC/RC validation, decision 0017) is out
  of scope.
