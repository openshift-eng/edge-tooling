---
name: weekly-health-report
description: Read-only coordinator skill that posts the weekly OpenShift Edge sprint and release health report (edge-context decision 0018) — a 3-line top post and at most 3 thread replies with owner lines, by sprint week and release phase
---

# Weekly Health Report (chai-bot coordinator)

> **Deprecated.** Replaced by [`weekly-report`](../weekly-report/SKILL.md), which runs the report as a script (`bin/weekly_report_files.py`). Kept only while the live persona still registers this playbook; it is removed once ship-help-bot#1415 (OCPEDGE-3276) is deployed.

This playbook is for the **chai-bot coordinator**, not for Claude Code. It
implements edge-context decision 0018 end to end: sprint health for the current
sprint plus one release section per release in progress, posted as one message
with details in its thread. It is **read-only** in Jira, asks no questions, and
uses no sub-agents.

## Every fetch is mandatory

Every fetch in the steps below is required: the release Feature gate, Product Pages, scrum-context, sprint health
(sprint issues, blocked-by links, the velocity sprints step 3 needs), each selected release section, epic fields, and
the Slack lookups of step 5. Run each one to its last page and finish every step before step 6 builds the post. Never
stop early, never post or summarize anything before step 6, and never decide the run is long enough: a long run is expected.

- **More match is normal.** `Showing the first 50 matching issues; more match:`
  is never a reason to retry, never makes a fetch incomplete and never puts it
  under Left out: fetch the page `next_page_jql` returns (Jira paging). Only a
  page `next_page_jql` reports as not whole (`PageNotWhole`) is sent again,
  whatever its header.
- **Retry once.** A tool call that fails, a `query_jira` page `next_page_jql`
  reports as not whole (`PageNotWhole`, Jira paging step 3), or a
  `get_jira_issue` response that
  `chai_jira_adapter.issue_text_complete(text, key)` rejects is sent again
  once, unchanged. The retry's answer takes its place; when it is whole, the
  fetch goes on as if nothing had failed. This applies inside every skill this
  report runs (scrum-context, sprint-health, release-health): where a skill
  says a failed call or a cut-off page makes the fetch incomplete, or not to
  retry, send it again once first.
- **Incomplete.** A fetch is incomplete only when a call or page still fails
  after its retry (a tool error, a permission error, text still cut off), or
  when the adapter reports it incomplete for a reason a retry does not change
  (`QueryError`, Jira paging steps 5 and 7; not sent again). A release
  with no Product Pages schedule is left out the same way. Taking several
  pages, or being split by project, never makes a fetch incomplete: it is
  complete when each part ended at `None` and `adapt_fetch` gives
  `fetch.complete` (Jira paging step 4). Pass a failed call as
  `{"error": "<message>"}` in its page slot, so the error is named.
- **Left out** lists only those fetches, each with its error, and the entries
  the scripts return themselves (for example "no release in progress", owners
  over the cap). Never list work that was not attempted: attempt it. A Slack
  lookup that still fails gives the name without a mention (step 5) and is not
  listed.
- **Sprint health incomplete → no report is posted.** Sprint health is incomplete when step 3 has no result, or a
  skipped one. No top post and no replies: a scheduled run delivers only the one-line status naming the fetch and its
  error, and a DM run ends silently (step 6). A failed blocked-by links or velocity fetch leaves only that signal out,
  and the report is posted.
- **Another section incomplete** → the report is posted without that section,
  and its fetch and error are on the Left out line.
- The only top post is step 6's `top_post_text`. Never write one yourself:
  never an "assessment incomplete" top post, and never a "0 people have items"
  top post without sprint health.

## Tools

| Tool | Used for |
|------|----------|
| `query_jira` | The release Feature gate (step 1) and the shared fetches (step 2b) |
| `get_jira_issue` | Links of the blocked candidates, the spikes and the block candidates only (step 2b); whole-value reads (Jira paging step 7) |
| Persona memory, `VELOCITY_STORE` (optional) | Closed-sprint velocity aggregates (Configuration) |
| Product Pages (`list_entities`, schedule reading, as scrum-context step 2 says) | Schedules of the gate's candidate releases |
| `scrum-context` skill | Current sprint and week, releases in progress, phases, availability |
| `sprint-health` skill | Sprint section (capacity or mid-sprint mode) |
| `release-health` skill | One release section per release in progress; the epic Target Version and QA Contact flags (once per report) |
| Slack user lookup by email (as the persona does) | Owner mentions |
| Slack post | The top post and its thread replies |
| `send_response`, `no_action_required` (coordinator tools, not the sandbox) | Step 6's end when sprint health is incomplete: the status line (scheduled run), a silent end (DM) |
| `sandbox_exec` (Python) | `plugins/edge-scrum/bin/chai_report.py` and the scripts the skills use |

All Jira reads follow the **Jira paging** recipe below, through the skills
(the release Feature gate in step 1 too).

## Configuration

```yaml
schedule: "Mondays 10:00 UTC"            # set on the chai-bot task, not here
channel: "#team-ocp-edge-notifications"
ROSTER_SOURCE: "openshift-eng/edge-context: people/team-roster.md"
VELOCITY_STORE: none                     # optional: "edge-scrum/velocity/v1" once provisioned
```

**Velocity store (optional).** Without it (the default), the closed sprints are
fetched every run. Prerequisite: a persona admin provisions a dedicated persona
memory namespace, `edge-scrum/velocity/v1/` (read, create-if-absent), and sets
`VELOCITY_STORE`; this playbook never provisions it or changes the persona's
configuration. It holds aggregate figures per closed sprint only
(`bin/velocity_cache.py`; no issue keys, summaries or people). `stored` is the
keys `velocity_cache.entry_key(11479, <sprint id>, <roster digest>)` of the
closed sprints (absent: no entry; unreadable store: `None`). An entry is
created once, from a complete fetch with usable velocity, used only when it
matches, and never refreshed (an admin clears it to re-read an edited sprint).

## Jira paging

> `Showing the first 50 matching issues; more match:` is normal. It is never an error, never partial data and never a reason to stop: call `next_page_jql` and fetch that page now, until it returns `None`.

`query_jira` returns text with no total count. Fetch every query this way, with
`max_results: 50` on every call:

1. First page: `chai_jira_adapter.page_jql(<JQL>)`, that is
   `(<JQL>) ORDER BY key DESC`. The one exception is release-health's feature
   fetch: one page ordered by Rank, for the cut line; if it says more match,
   the whole query is fetched by key, starting with the query `next_page_jql`
   returns (release-health "Features keep PM rank").
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
   the report's step 6 ("Build and post") decides what is posted, and the fetch
   is listed under "Left out" with its error.
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

**Example** (synthetic project `TESTA`):

1. Send `(project = TESTA AND issuetype = Feature) ORDER BY key DESC`. The answer
   starts `Showing the first 50 matching issues; more match:` and its last issue
   is `TESTA-251`: `next_page_jql` returns
   `(project = TESTA AND issuetype = Feature) AND key < TESTA-251 ORDER BY key DESC`.
   Send it now.
2. Again the first 50, last issue `TESTA-201`: send
   `(project = TESTA AND issuetype = Feature) AND key < TESTA-201 ORDER BY key DESC`
   (the new `key <` replaces the previous one).
3. That page starts `Found 7 issues (showing 7):`: `next_page_jql` returns
   `None`. Three pages, 107 issues: the fetch is complete.

## Steps

### 1. Where are we

1. **Release Feature gate.** Releases are reported only when Edge Features
   target them. One query, paged by key (Jira paging):
   `project = OCPSTRAT AND issuetype = Feature AND labels in ("ocpedge-plan", "microshift") AND status != Closed AND "Target Version" is not EMPTY`
   — `extra_fields` `chai_jira_adapter.EXTRA_FIELDS["feature_gate"]`
   (`customfield_10855, customfield_10795, customfield_10470, customfield_10473,
   customfield_10475`: the Target Version, and the features' Size, QA Contact,
   Doc Contact and SME, so the same rows also give the refinement features,
   step 2b). Adapt all pages once
   (name `feature_gate`) as `gate_fetch`, then in the sandbox:

   ```python
   gate = chai_report.release_feature_gate(gate_fetch)
   candidates = gate["releases"]  # releases with at least one Edge Feature; [] when the gate is incomplete
   ```

   Only non-Closed Features count (Initiatives do not), once per Target Version
   `openshift-X.Y`. Run this block even when the gate fetch is incomplete, and
   never pass `gate=None` on: a failed or cut-off gate fetch is **unknown**,
   never zero, so no release section is reported and the Left out line says
   why. A shortened value in a field the gate does not read (SME, QA or Doc
   Contact, Size) leaves the gate complete; only the section holding that
   feature is left out.
2. **Product Pages:** read the schedules of the `candidates` and the previous
   release of each, as scrum-context step 2 says
   (`list_entities(kind="product", shortname="openshift")` for
   `ocp_product_id`, then `list_entities(kind="release",
   public_parent_id=ocp_product_id)` with `cursor=next_cursor` for further
   pages; no phase filter). Read them once and pass the same `schedules` to every
   scrum-context run in items 3 and 4. A release whose lookup still fails after
   the retry is passed as `{"error": "<reason>"}`; its section is left out
   ("release phase unknown: Product Pages lookup failed: " and that error) and the rest of the
   report goes on.
3. Run **scrum-context** with no argument: `current_sprint` (with `week`),
   `current_sprint_availability`, `release_in_progress`. Its board listing
   (`list_jira_board_sprints`, all states) is read **once** for the whole
   report and kept: it also gives the velocity sprints
   (`scrum_context.last_closed_sprints(listing, 11479, <current sprint number>)`,
   step 3) and each refinement sprint's state (release-health refinement step
   5), so no second listing is read.
4. Run **scrum-context** once per release in `candidates`, with that release as
   the argument. Collect each result's `release` value (the window, not the
   whole result) as `windows`:
   `windows = [result["release"] for result in release_results]`.

### 2. Choose the sections

In the sandbox:

```python
sections, missing = chai_report.select_sections(context, windows, gate)
```

| Release phase (scrum-context) | Release reply: feature checks (`_scrum_rules`) |
|---------------|---------|
| `refinement` (the refinement sprint, every week) | every non-Closed Feature (not Initiative) targeting the release: no SME (alone, on the leads' line), no QA Contact, no Doc Contact, epic with no owner, unsized, no epics, empty epic, no refinement spike (week 1: one count line), spike not in the refinement sprint, test plan spike missing (QE Lead's line) |
| `development` | scope vs capacity; per Feature: no SME (alone), refinement overdue, behind schedule, blocked, epic blocked, Blocked / Parked label, below the cut line, no QA Contact, no Doc Contact, epic with no owner |
| `pencils-down sprint` | the same, flagged as the pencils-down sprint |
| `pre-branch-cut` | the same (work can still land before branch cut) |
| `not scheduled yet`, an anchor not placeable on a sprint, or phase unknown | listed in `missing`, with the reason |
| `release phase unknown: Product Pages lookup failed` | listed in `missing` with that reason and the lookup's error (a lookup with no schedule: "no Product Pages schedule yet") |
| `not started`, `finished` | no release reply |
| no Edge Feature targets the release (gate complete) | no release reply and nothing under Left out about it, whatever Product Pages says |
| gate fetch incomplete | no release reply; Left out: `Release sections: release Feature gate incomplete: <reason>` |

Two releases in progress share the one release reply (one header part each).
No release in progress → no release reply, and the Left out line says "no
release in progress"; sprint health is still reported. When the gate is
complete and no release has an Edge Feature, nothing about releases is said
under Left out: no release was expected.

| Sprint week | Sprint reply |
|---|---|
| 1 (also a one-week sprint's last week) | capacity: sprint SP vs capacity; unpointed, no epic |
| 2 up to the week before the last | mid-sprint: burndown; explicit blocks (blocked by KEY, Blocked label, Parked) |
| last (by the sprint's dates), week 2 or later | "final week" in place of "week N, mid-sprint"; done vs expected and explicit blocks, plus "N still open (X SP): A in review or testing (B SP), C in progress or to do (D SP)" (and "E other status (F SP)"); no carry-over items, no projected finish, no re-scope line |

### 2b. Shared fetches (one plan for the whole report)

Every Jira list is fetched **once** for the whole report, not per skill or
section, as the Jira paging recipe says. A wave's fetches go out together and
give the next wave's queries. Each shared query unites the per-section queries
without changing them; each part is split back out by its own predicate.

1. **Wave 1** (after step 2): the closed sprints velocity still needs (step 3);
   the sprint issues and, in mid-sprint mode, the blocked candidates
   (sprint-health step 3); for each release-health section, its rank-ordered
   features page and its unlinked bugs (release-health step 4, items 1 and 4); for each
   section, its refinement-sprint spikes (release-health refinement step 3). No
   current sprint id (board listing failed, sprint not on the board yet): skip only
   the sprint issues and blocked candidates (and, with no listing, the velocity
   sprints); every other fetch still runs, and step 3 records why.
2. **Features.** A refinement section's features are
   `chai_report.gate_features(gate_fetch, release)` (the gate's rows narrowed to
   the release and a resolution other than Duplicate or Obsolete: exactly the
   refinement features query; no query). A release-health section's features are its
   rank-ordered page when it is the last one (the cut line is computed), else the
   whole query by key (the cut line is left out).
3. **Wave 2: one release epics fetch** for every section,
   `project in (OCPEDGE, USHIFT) AND "Parent Link" in (<the features of every section>)`,
   at most 50 feature keys per query (`extra_fields` `EXTRA_FIELDS["epics"]`).
   Adapt all its queries as one fetch, then, with the key sets the queries used,
   `parts = chai_report.partition_epics(epics_fetch, {release: <its feature keys>})`:
   `parts[release]` is that release's epics query (any status, Closed epics
   included; a release with no feature: none, complete; no feature at all: no
   query). The decision 0024 epics (release-health step 5.1, `EXTRA_FIELDS["field_epics"]`)
   stay their own fetch, `field_epics_fetch`: another predicate, so neither decides
   the other's rows. In the same wave: the spikes that block each feature, one query per 20 features of
   one section (`chai_jira_adapter.linked_spikes_jql(keys)`: the per-feature
   `linkedIssues` queries joined by OR), and the links of the blocked
   candidates and the refinement-sprint spikes.
4. **Wave 3:** one stories fetch, `project in (OCPEDGE, USHIFT, OCPBUGS) AND parent in (<the epics of every section>)`,
   grouped, with the epic keys the query used, by `chai_report.group_stories(stories_fetch, {release: <its epic keys>})`
   (an issue counts in every release whose epics include its parent; a release with no epics: none, complete); a refinement
   section's `counts, why = chai_report.epic_issue_counts(stories_fetch, <its epic keys>, <the query's epic keys>)`
   (counts None, a page not whole or a story it cannot attribute: only its empty-epic check is left out, with `why`
   as `refinement_gaps(..., epic_issue_counts_why=why)`; a section with no epic: `{}, None`; no epic in any section: no stories query); one block candidates fetch,
   `key in (<the features and epics of every release-health section>) AND issueLinkType = "is blocked by"` (a section with none: nothing to check, links `{}`);
   the epic-field parents still to read (`batch, parents_to_read = chai_report.reuse_parents(<field_epics_fetch's parent keys>, [<the features fetches>, gate_fetch])`,
   the latest read first, reuses a parent only when one of these fetches returned it whole), then
   `key in (<parents_to_read>)`, up to 40 keys per query; and the links of the
   returned linked spikes not read yet. Split each linked-spike query per feature
   with `chai_jira_adapter.split_linked_spikes(<its keys>, linked_fetch, link_texts)`:
   a spike counts for every feature it blocks, by its own links; a spike whose
   links cannot be read leaves that query's features incomplete.
5. **Wave 4:** the links of the block candidates only (each key read once per report with `get_jira_issue(<key>, fields="issuelinks")`).

A shared fetch still failing after its retry is left out, with its error, by
every part it serves (a failed epics fetch: every release section). A problem
of one issue (a shortened value) concerns only its parts.

### 3. Sprint health

Run **sprint-health** (week 1 → capacity mode, later → mid-sprint mode; no
prompt) with the shared sprint issues, blocked candidates and links of step 2b.
Compute velocity once over the last 3 closed sprints of the board listing
(`closed = scrum_context.last_closed_sprints(...)`, step 1.3). With a velocity
store: `hits, to_fetch = velocity_cache.plan(closed, stored, 11479,
velocity_cache.roster_digest(roster))` (`stored`: what the store returned, None
when unreadable); fetch only `to_fetch` (newly closed, or no valid entry), run
`compute-velocity.py` on those, then `velocity_cache.combine(closed, hits,
fresh)`. Without a store, fetch all three. A velocity (closed-sprint) fetch still
incomplete after its retry: velocity is not available, that fetch and its error
the reason (sprint-health step 4); nothing is combined or stored. Else check it with
`chai_report.usable_velocity`. Reuse the value **and its reason** for every release section (`velocity_reason=` in `sprint_health` and
`release_health`), so the Left out line says why capacity is missing. In capacity mode, the current sprint's
capacity = velocity × `current_sprint_availability`; when that is not known,
capacity is not adjusted and is labelled "not PTO-adjusted". Mid-sprint mode
applies no availability adjustment.

Sprint health is complete only when sprint-health returns a `sprint_health`
result that is not skipped. When it returns none (current sprint not available
or not on the board yet, or the sprint issue fetch still incomplete after the
retry), set `sprint = None` and add `{"what": "Sprint health", "why":
"<reason>"}` to `missing`, naming the failed fetch and its error (for example
`sprint issue fetch incomplete: <error>`, or `board sprint fetch failed:
<error>` when scrum-context could not read the board's sprints). A skipped
result (sprint week unknown) is kept as `sprint`: it carries its own reason.
Taking several pages, or being split by project, never makes the sprint issue
fetch incomplete. The other steps still run; step 6 then posts no report.

### 4. Release sections

For each selected section, run **release-health** for that release (it uses the
window and phase already read) with the shared parts of step 2b in place of
its own fetches: features, epics, stories, bugs, spikes, block candidates and
links. Check each part in the order release-health fetches it, so a section
names the same first failure it named with its own fetches. Collect the **computed** results
(`release_health` or `refinement_gaps` output) in a new list, `computed`; add
any section it returns as missing to `missing`. `select_sections` only chooses
sections: never pass its descriptors to `build_report`.

Then run release-health's **epic Target Version and QA Contact** step (step 5,
decision 0024) **once** for the report, whatever the number of release
sections, with the fetches of step 2b:
`gaps = chai_report.epic_field_gaps(field_epics_fetch, ([batch] if batch else []) + parent_batches,
previous_flagged)`. `previous_flagged` is last week's `gaps["flagged_keys"]`
from persona memory, or `None` when memory doesn't have it (then no epic is
marked 🆕). If its fetches still fail after the retry, the result lists them
with the error; every other section is posted as usual.

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
failure = chai_report.sprint_health_failure(sprint, missing)  # None when sprint health is complete
if failure is None:
    report = chai_report.build_report(sprint, computed, missing, mentions, team_owner=scrum_master,
                                      epic_gaps=gaps)
    top_post_text = report["top_post_text"]  # one string: the channel message
    replies = report["replies"]  # strings, one per thread reply, in order
```

**Before posting:** when `failure` is a line, sprint health is incomplete (step 3 has no result, or a skipped one; never because a fetch had several pages or was split by project). This is the report's only "no report" exit (an incomplete section is posted without that section).
Post no report (no top post, no replies, no figures, attachments or buttons, no plain-text final message) and store nothing in
persona memory (no velocity entries, no flagged keys). End the run with these coordinator steps (not in the sandbox), in order:

```text
scheduled run: 1. register one response element, its whole content: failure
scheduled run: 2. send_response(mode="report", result_ids=[])
interactive run (DM): 1. no_action_required(reason=failure)
```

`failure` is the line itself, for example `Weekly health report not posted: Sprint health incomplete: sprint issue fetch incomplete: fetch: page 1: query_jira call failed: <error>`.
Otherwise post the report: a section that is incomplete is not in `replies`, and its fetch and error are on the Left out line.

`report["top_post"]` is the list of the top post's lines, for checks; post
`top_post_text`, never the list.

- **Top post** = `top_post_text`, exactly three lines: status, sprint,
  week, mode and release phase; the biggest risk (or "No risks this week" with
  the sprint figures); "N people have items: @A, @B, …" (everyone tagged in
  any reply, epic fields included; at most 8 names, then "+N more").
- **Thread replies** = `replies`, **at most 3**, in this order:
  1. sprint health (with the final-week view in the sprint's last week);
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

  After posting, with a velocity store, create
  `velocity_cache.creates(closed, fresh, <complete sprint ids>, stored, 11479, digest)`
  (create-if-absent; an existing entry is never changed); nothing is written
  without a store or when it could not be read.

  After posting, store `gaps["flagged_keys"]` in persona memory if the persona
  keeps memory, for next week's 🆕 markers, **only when `gaps["complete"]` is
  true and this is the scheduled channel report**: an epic run with an
  incomplete fetch (Jira paging step 3 or 5–7), or a run from a DM, must not
  replace last week's set.

Post `top_post_text` to the channel, then each string of `replies` in its
thread. Run from a DM (validation or an extra run requested by a task manager),
reply in the DM instead of the channel.

## Rules

- **Team level only.** No per-person load, SP or PTO. A name appears only as the
  owner of items on an owner line.
- **Never estimate.** An incomplete fetch is left out with its error, and a
  release with no schedule with that reason (for example "9.2 release section:
  no Product Pages schedule yet"); no figure is computed from either. When it
  is sprint health, no report is posted (step 6).
- **Nothing to flag** (sprint health complete) → the top post says "No risks
  this week" with the sprint figures, and "0 people have items". Epic flags
  never change the colour or the risk line; their owners count on the third
  line.
- **Explicit blocks only.** Blocked means an open "is blocked by" link or a
  Blocked / Parked label; there is no stale (no recent update) check.
- **Read-only** in Jira. Release testing (EC/RC validation, decision 0017) is out
  of scope.
