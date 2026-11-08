---
name: weekly-health-report
description: Read-only coordinator skill that posts the weekly OpenShift Edge sprint and release health report (edge-context decision 0018) — a 3-line top post with details and owner-mentioned action items in its thread
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
| `release-health` skill | One release section per release in progress |
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

1. First page: `(<JQL>) ORDER BY key DESC` with `max_results: 50`.
2. While the results header — the first such line, after any "Create metadata"
   block — is `Showing the first 50 matching issues; more match:` (the page then
   ends with the "... More issues match" hint), fetch the next page:
   `(<JQL>) AND key < <last key on the page> ORDER BY key DESC`.
3. Stop when a page has the header `Found N issues (showing N):` or fewer than
   50 issues.
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

| Release phase | Section |
|---------------|---------|
| `refinement` | release-health, refinement mode: refinement and test-plan spike gaps |
| `development` | release-health: capacity vs scope, cut line, features at risk |
| `pencils-down sprint` | the same, flagged as the pencils-down sprint |
| `pre-branch-cut` | the same (work can still land before branch cut) |
| `not scheduled yet`, an anchor not placeable on a sprint, or phase unknown | listed in `missing`, with the reason |
| `not started`, `finished` | no section |

Two releases in progress each get their own section with their own phase. No
release in progress → `missing` says "no release in progress"; sprint health is
still reported.

### 3. Sprint health

Run **sprint-health** (week 1 → capacity mode, later → mid-sprint mode; no
prompt). Compute velocity once (`compute-velocity.py`, last 3 closed sprints),
check it with `chai_report.usable_velocity`, and reuse the value **and its
reason** for every release section (`velocity_reason=` in `sprint_health` and
`release_health`), so the "Left out" reply says why capacity is missing. In capacity mode, the current sprint's
capacity = velocity × `current_sprint_availability`; when that is not known,
capacity is not adjusted and is labelled "not PTO-adjusted". Mid-sprint mode
applies no availability adjustment.

### 4. Release sections

For each selected section, run **release-health** for that release (it uses the
window and phase already read). Collect the **computed** results
(`release_health` or `refinement_gaps` output) in a new list, `computed`; add
any section it returns as missing to `missing`. `select_sections` only chooses
sections: never pass its descriptors to `build_report`.

### 5. Owner mentions

Each action item names its owner: the SME (feature-level items), the QE Lead
(test plan spikes), or the assignee (sprint issues). Team-level items (re-plan
or re-scope the sprint) belong to the **Scrum Master**:
`chai_jira_adapter.find_scrum_master(team_roster_md, scrum_master_md)` reads it
from a roster row whose role contains "Scrum Master", or a
`Current Scrum Master: <name>` line in edge-context `roles/scrum-master.md`.
When neither names one, those items keep "no owner set". For each distinct owner:

1. Find the owner in `ROSTER_SOURCE` by display name
   (`parse_team_roster(...)["contacts"]`, matched case- and space-insensitively).
2. Their Rover ID is the last part of the profile link; email = `<Rover ID>@redhat.com`.
3. Look up the Slack user by that email; the mention is `<@USER_ID>`.
4. No roster row, no Rover link, or no Slack user → the name, without a mention.

Pass the result as `mentions = {display name: "<@USER_ID>"}`. Whether a mention
pings follows the platform's **Allow Mentions** setting for that person; the
report does not decide it. Do this lookup outside the sandbox.

### 6. Build and post

```python
report = chai_report.build_report(sprint, computed, missing, mentions, team_owner=scrum_master)
```

- **Top post** = `report["top_post"]`, exactly three lines: status and where we
  are; the biggest risk (or "No risks this week" with the sprint figures);
  "N items need action".
- **Thread replies** = `report["replies"]`, in order: sprint health, one reply per
  release section, action items (each with its owner), and "Left out" (missing
  sections and signals, with reasons).

Post the top post to the channel, then each reply in its thread. Run from a DM
(validation or an extra run requested by a task manager), reply in the DM
instead of the channel.

## Rules

- **Team level only.** No per-person load, SP or PTO. A name appears only as the
  owner of one action item.
- **Never estimate.** If a source is missing or a fetch is incomplete, post what is
  complete and list what was left out and why (for example
  "9.2 release section: no Product Pages schedule yet"). No figure is computed
  from an incomplete fetch.
- **Nothing to flag** → the top post says "No risks this week" with the sprint
  figures, and "0 items need action".
- **Read-only** in Jira. Release testing (EC/RC validation, decision 0017) is out
  of scope.
