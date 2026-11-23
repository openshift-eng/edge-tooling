---
name: release-health
description: Read-only coordinator skill that reports team-level feature health for one release in its current phase — refinement gaps (staffing, size, epics, refinement and test plan spikes) in the refinement sprint; scope vs capacity, schedule, blocks, cut line and staffing from development through pre-branch-cut — plus the epic Target Version and QA Contact flags of decision 0024
---

# Release Health (chai-bot coordinator)

This playbook is for the **chai-bot coordinator**, not for Claude Code. For one
release (`X.Y`) it reports the release section of the weekly health report
(edge-context decision 0018), chosen by the release's phase. It is **read-only**
and asks no questions; there are no sub-agents. Output is **team level only**:
the per-person "above the guideline" flags of release-planning are dropped.

## Tools

| Tool | Used for |
|------|----------|
| `scrum-context` skill | The release's window, phase and per-sprint availability |
| `list_jira_board_sprints` | Closed sprints on board 11479 (velocity) |
| `query_jira` | Features, epics, stories, bugs, spikes (paged; see Jira paging) |
| `get_jira_issue` | Issue links of refinement-sprint spikes |
| `sandbox_exec` (Python) | `plugins/edge-scrum/bin/` scripts on the fetched data |

Jira credentials are not available in the sandbox: fetch with the tools above,
then pass the text into `sandbox_exec`.

## Configuration

```yaml
board_id: 11479
ROSTER_SOURCE: "openshift-eng/edge-context: people/team-roster.md"
focus_priorities: [Blocker, Critical, Major]   # run-checks defaults
include_lower_when: [Refinement]
fields:  # request fields by id only
  story_points:   customfield_10028
  sprint:         customfield_10020
  qa_contact:     customfield_10470   # QE Lead on features
  sme:            customfield_10475
  docs_approver:  customfield_10473
  tshirt_size:    customfield_10795
  target_version: customfield_10855
  parent_link:    customfield_10018
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
   `chai_jira_adapter.adapt_fetch`; it rechecks headers, page sizes and key order
   and returns `fetch.complete`. If a call failed or a page was cut off, the fetch
   is **incomplete**: compute nothing from it and list it as left out, with the
   reason.
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

**Features keep PM rank.** Fetch features once with `(<JQL>) ORDER BY Rank ASC`,
`max_results: 50`, and adapt with `"order": "rank"`. If that page has the
`Found N issues (showing N):` header, the order is the PM rank. If it shows only
the first 50 ("more match"), do not page on from it; fetch the features again
from step 1 of the key recipe above. Scope and risk can still be computed, but
the **cut line is left out** (`release_health(..., rank_available=False)`),
since key order is not rank. Keep the `adapt_fetch` result of the features fetch
you used as `features_fetch`; `chai_report.rank_order_available(features_fetch)`
is true only for a complete `"order": "rank"` fetch, and is the `rank_available`
to pass (never assume rank order).

## Steps

### 1. Window and phase

Run the **scrum-context** skill with the release (`X.Y`). Use `phase`,
`refinement_sprint`, `pencils_down_sprint`, `branch_cut_sprint`, the current
sprint, and `sprint_availability`.

| Phase | Mode |
|-------|------|
| `refinement` | Refinement (step 3) |
| `development`, `pencils-down sprint`, `pre-branch-cut` | Release health (step 4); in the pencils-down sprint, flag it, nothing else changes |
| anything else | No section: return it as missing with the phase as the reason |

If an anchor the mode needs is "not scheduled yet" (the refinement sprint for
refinement; the refinement and branch-cut sprints for release health), return
the section as missing ("a release milestone could not be placed on a sprint").

### 2. Roster

Read `ROSTER_SOURCE` and run `chai_jira_adapter.parse_team_roster(markdown)`
in the sandbox; write `{"members", "contacts"}` to `roster.json`. Pass it to every
`adapt_fetch` call so display names match the roster.

### 3. Refinement mode

The release-health `--refinement` checks, for **every non-Closed OCPSTRAT
Feature targeting the release, in any status** (New, Refinement, In Progress):
features are often still New when the refinement sprint starts. Initiatives
are not checked.

1. **Features**, paged:
   `project = OCPSTRAT AND issuetype = Feature AND labels in ("ocpedge-plan", "microshift") AND "Target Version" = "openshift-X.Y" AND status != Closed AND (resolution is EMPTY OR resolution not in (Duplicate, Obsolete))`
   — fields `summary, status, issuetype, priority, assignee, fixVersions, labels,
   customfield_10795, customfield_10470, customfield_10473, customfield_10475`
   (Size, QA Contact, Doc Contact, SME; each prints as `customfield_N: value`,
   and an unset field prints no line). None → the section says "0 feature(s)
   checked".
2. **Epics**, paged, in batches of 50 feature keys:
   `project in (OCPEDGE, USHIFT) AND "Parent Link" in (<keys>)` — fields
   `summary, status, assignee, labels, parent, updated, fixVersions,
   customfield_10028, customfield_10795, customfield_10855, customfield_10018,
   customfield_10470`. Run `transform-epics.py`.
   **Epic issue counts** (for "empty epic"), paged, in batches of 20 epic keys:
   `project in (OCPEDGE, USHIFT, OCPBUGS) AND parent in (<epic keys>)` — field
   `parent`; count the issues per parent epic: `{epic key: count}`. Incomplete →
   pass `None` (the empty-epic check is left out).
3. **Refinement-sprint spikes**, paged:
   `project in (OCPEDGE, USHIFT) AND issuetype = Spike AND sprint = <refinement sprint id>`
   — fields `summary, status, assignee, resolution`. `query_jira` cannot return
   issue links, so call `get_jira_issue(<spike key>, fields="issuelinks")` on
   each spike (`fields="customfield_10020,issuelinks"` when the same read also
   supplies a whole Sprint value) and attach its links with
   `attach_issue_links` (needed to see spikes that block a child epic instead of
   the feature). Its Requested fields area (after the description) says
   `issuelinks: (empty, or not a field of this issue)` (no links) or
   `issuelinks: (set, but get_jira_issue cannot render this field's structure)`
   (the "Linked Jira Issues" section is read). A missing,
   unknown or cut issuelinks state never means "no links". If it returns any
   problem (a failed call, a response for another issue, a state it cannot use,
   or a response cut off or not readable), the refinement section is left out
   ("spike links incomplete"): a missed link would report a false missing spike. A
   projected refinement sprint (no id) → refinement section missing
   ("refinement sprint is not on the board yet").
4. **Spikes that block each feature**, one paged query per feature:
   `issue in linkedIssues("<feature key>", "is blocked by") AND issuetype = Spike`
   — fields `summary, status, assignee, resolution`. Attach them with
   `chai_jira_adapter.attach_linked_spike_fetches(features, {feature_key: adapted_fetch})`
   (the whole `adapt_fetch` result per feature; an incomplete one marks the
   features fetch incomplete, so the refinement section is left out)
   **before** `transform-features.py`; they merge with the refinement-sprint
   spikes in `transform-spikes.py`.
5. Read the refinement sprint's state from `list_jira_board_sprints` for board
   11479 (all states; match the refinement sprint's id): scrum-context's window
   has no state. Write `{"refinement_sprint_closed": <that state is closed>}` as
   `sprints.json`, then run `transform-features.py`, and `transform-spikes.py`
   with `--input <refinement-sprint spikes> --linked-spikes <all linked spikes>
   --features-file --epics-file --sprints-file`.
6. `chai_report.refinement_gaps(features, spikes, section, refinement_sprint_closed,
   epics=epics, epic_issue_counts=counts, week=<current sprint week>)` runs
   `_scrum_rules.refinement_findings` on Features only. A feature with **no SME**
   (`customfield_10475` empty) gets only "no SME": all of them go on one line,
   "Leads, assign SMEs (@Scrum Master): N feature(s) have no SME: KEY, …
   (+N)", and their other problems are not listed until an SME is set. In the
   refinement sprint's **week 1**, "no refinement spike" is one count line, "N
   feature(s) need a refinement spike this sprint", and never the biggest risk;
   from week 2 it is listed per owner. Other labels, per feature: "no QA Contact", "no Doc Contact", "epic KEY has no
   owner" (epic assignee), "unsized" (no Size), "no epics", "empty epic KEY",
   "no refinement spike", "spike not in the refinement sprint" (refinement-spike
   rules, first match wins: Closed → fine; missing but refined via epics → fine;
   missing → 🔴; open but not in the refinement sprint → 🟡; open in it → in
   progress), and "test plan spike missing" (on the **QE Lead's** line; skipped
   when the QA Contact is empty or `unassigned_jira`; a Closed test plan spike
   passes whatever its resolution). There is no acceptance-criteria check.
   Owner of a feature's line: its **SME** only, never the feature's assignee.

### 4. Release-health mode (development, pencils-down sprint, pre-branch-cut)

Capacity vs scope and the cut line, with release-planning's `run-checks.py`,
and the feature checks of `_scrum_rules.development_findings`.

1. **Features**, rank-ordered (see Jira paging):
   `project = OCPSTRAT AND issuetype = Feature AND labels in ("ocpedge-plan", "microshift") AND "Target Version" = "openshift-X.Y" AND (resolution is EMPTY OR resolution not in (Duplicate, Obsolete))`
   — fields `summary, status, issuetype, priority, assignee, fixVersions, labels,
   customfield_10795, customfield_10855, customfield_10470, customfield_10473,
   customfield_10475`. Run `transform-features.py`. No features → section missing
   ("no features target X.Y").
2. **Epics** as in refinement step 2; `transform-epics.py`.
3. **Stories**, paged, in batches of 20 epic keys:
   `project in (OCPEDGE, USHIFT, OCPBUGS) AND parent in (<keys>)` — fields
   `summary, status, issuetype, priority, assignee, labels, updated, parent,
   customfield_10028, customfield_10020`. `transform-stories.py --today`.
4. **Unlinked bugs**, paged:
   `project = OCPBUGS AND component in ("Installer / Single Node OpenShift", "Two Node with Arbiter", "Two Node Fencing", "Logical Volume Manager Storage", "MicroShift", "MicroShift / Networking", "MicroShift / Storage") AND fixVersion in ("X.Y", "X.Y.0") AND "Epic Link" is EMPTY`
   — fields `summary, status, priority, assignee, components, labels, updated`.
   `transform-bugs.py --today`.
5. **Velocity**: `compute-velocity.py` over the last 3 closed board sprints, as in
   the sprint-health skill (step 4). The weekly report computes it once and
   passes it in. Incomplete, or `chai_report.usable_velocity` returns a reason
   → velocity not available: run `run-checks.py`
   without `--velocity-file`; the section then reports scope only and lists
   capacity and the cut line as left out (its roster-sum fallback
   would be an estimate).
6. **Remaining sprints and availability**: the `sprint_availability` entries from
   the current sprint through the **branch-cut sprint** (projected sprints
   included). `chai_report.release_capacity(team_velocity, entries, today)` gives
   the count, `availability_csv` and the label. The current sprint counts only
   for its remaining working days (velocity × availability × remaining working
   days ÷ sprint working days, today included); upcoming sprints count in full.
   A sprint with unknown availability counts as 1.0 and is named as "not
   PTO-adjusted".
7. Run:

   ```bash
   python3 run-checks.py --features features.json --epics epics.json \
     --stories stories.json --bugs bugs.json --roster roster.json \
     --remaining-sprints <count> --velocity-file velocity.json \
     --availability <availability_csv> --version X.Y --today <today> \
     --first-sprint <refinement sprint number> --pencils-down <branch-cut sprint number> \
     --focus-priorities Blocker,Critical,Major --include-lower-when Refinement \
     --no-hidden-scope-estimate --output checks.json
   ```

   `--no-hidden-scope-estimate` keeps run-checks from estimating the SP of
   unpointed work (decision 0018: never estimate); the section reports "N
   unpointed items, not counted in scope" instead. `--pencils-down` is the
   branch-cut sprint: work can still land until branch
   cut, so the pencils-down sprint is flagged but not treated differently.
   Omit `--velocity-file` when velocity is not available.
8. **Refinement spikes, again:** refinement steps 3–5 for the release's
   refinement sprint, which is now closed (`refinement_sprint_closed` = true),
   so an open refinement spike shows as "refinement overdue".
9. **Feature and epic blocks:** `query_jira`, paged:
   `key in (<feature and epic keys>) AND issueLinkType = "is blocked by"`;
   `get_jira_issue(<key>, fields="issuelinks")` on each result, then
   `chai_jira_adapter.attach_issue_links(adapted, {key: text})` and
   Start with `links = {}` and `links_unread = []`. Set
   `links = {key: _jira_transforms.extract_blocked_by(issue)}` (open blockers
   only) for every issue whose links were read (the issuelinks state "empty"
   is an issue with no links). Keys whose `get_jira_issue` failed, or whose
   response had no usable issuelinks state, was cut off or not readable (the
   keys in the problems `attach_issue_links` returns, including a block row it
   could not read) are **not** guessed: pass them as `links_unread=[...]`;
   confirmed blockers are kept.
   If the candidate query itself is incomplete, pass `links=None` (block check
   not run). Either way the header's on-track count says the blocks were not
   checked, and the Left out line says for which keys.
10. `chai_report.release_health(checks, section, capacity,
    chai_report.rank_order_available(features_fetch),
    velocity_reason, features=features, epics=epics, spikes=spikes,
    stories=stories, links=links, links_unread=links_unread,
    current_sprint_number=<current sprint number>)`:
    scope vs capacity (header), and per Feature (owner: the SME only; a feature
    with no SME gets only "no SME", on the leads' line): "refinement overdue", "behind schedule: X% done vs Y% expected",
    "blocked by KEY", "epic KEY blocked by KEY", "Blocked label", "Parked",
    "below the cut line", "no QA Contact", "no Doc Contact", "epic KEY has no
    owner". Per-person capacity rows and `ABOVE_GUIDELINE` flags are dropped;
    roster drift becomes a count with its SP, listed as left out.

    **Behind schedule** (`_scrum_rules.expected_pct`, `done_pct`): development
    sprints = the board sprints after the refinement sprint up to and including
    the branch-cut sprint, those between pencils down and branch cut included,
    counted by position; **expected %** = development sprints finished before
    the current sprint ÷ development sprints × 100; a feature's **% done** =
    done SP ÷ total SP of its epics' issues (law 07 done states; by issue count
    when none is pointed). Flagged when % done < 75% of expected %, both
    compared unrounded (a tie is not behind; percentages are rounded only for
    display). Closed and Release Pending features are not checked. When the
    development window is not known, or the schedule check could not run (no
    epics or epic issues), the header says "schedule not checked" and gives no
    on-track count; the other feature checks are still listed.

### 5. Epic Target Version and QA Contact (decision 0024)

Flags for active OCPEDGE epics with a parent (edge-context decision 0024). They
do not depend on the release: the weekly report runs this step **once**, not
once per release section. Epic statuses use the exact law 04 names: **active**
epics are To Do, In Progress, Dev Complete, Testing and Release Pending (not
New or Planning, which are still being refined, and not Closed).

1. **Active epics with a parent**, paged by key until complete (see Jira paging):
   `project = OCPEDGE AND issuetype = Epic AND status in ("To Do", "In Progress", "Dev Complete", "Testing", "Release Pending") AND parent is not EMPTY`
   — fields `summary, status, assignee, parent, customfield_10855,
   customfield_10470`. Adapt with `adapt_fetch` (name `epics`).
2. **Their parents**, by key, in batches of up to 40 distinct parent keys (so
   each batch fits on one page):
   `key in (<parent keys>)` — fields `summary, issuetype, status,
   customfield_10855, customfield_10470`. Adapt each batch with `adapt_fetch`
   and keep the keys it asked for: `{"keys": [<keys sent>], "result": <adapted>}`.
   If a batch call fails or its fetch is incomplete (for example a JQL error
   naming a key you cannot see), query that batch's keys again one at a time
   (`key = <KEY>`), each as its own `{"keys": [<KEY>], ...}` entry; a key that
   still fails is recorded as `{"keys": [<KEY>], "error": "<the reason given>"}`.
3. `gaps = chai_report.epic_field_gaps(epics, parent_batches, previous_flagged)`
   flags active epics only (any other status returned is ignored). Each
   problem has a label, in full words:
   - **"no Target Version"** (all active epics);
   - **"no QA Contact"** (all active epics; `unassigned_jira` counts as none),
     noting the parent as decision 0024 asks: "no QA Contact" when the feature
     has one, "no QA Contact, feature has none", or "no QA Contact, feature
     could not be read" (never the parent's QA Contact name);
   - **"Target Version ≠ feature"** (To Do and In Progress only, and only when
     the parent has a Target Version): the epic's version is not among the
     parent's version names (a parent with two versions matches either); the
     owner decides: retarget, split or keep? From Dev Complete on, finished work
     keeps its release;
   - **"feature closed"** / **"feature in backlog"** (all active epics): the
     parent is Closed or in Backlog;
   - **"no assignee"** (all active epics): the epic goes on the **Scrum
     Master's** line, as team-level action items do.

   `previous_flagged` is the previous report's flagged epic keys, if the
   persona's memory holds them; otherwise pass `None` and no epic is marked new.
   Never reconstruct the previous set. If the persona can keep memory, store
   `gaps["flagged_keys"]` after the **scheduled channel** report, for next
   week (never after a DM run), **only when
   `gaps["complete"]` is true** (a failed or partial run would make next
   week's returning flags look new).

   An empty field prints no line, so a missing line is an empty field.
4. Parents that could not be read (restricted, deleted, no permission, a failed
   or cut-off page) are never guessed: those epics skip the two checks that need
   the parent, and the result lists them as left out with the reason. An
   incomplete epic fetch computes nothing; the rest of the report is unaffected.

The flags are one thread reply with **one line per owner** (the epic's
assignee, or the Scrum Master for epics with no assignee):
`• <owner>: OCPEDGE-1234 (no Target Version, no QA Contact), OCPEDGE-5678 (feature closed)`
— full issue keys and labels only, no summaries, versions or parent keys.
Within a line, epics with the most problems come first, then by key; 🆕 marks
an epic not flagged in the previous report (only when `previous_flagged` was
given). Owners with the most epics come first. The first line is
`*Epic fields* (N epics: X no Target Version, Y no QA Contact, Z Target
Version ≠ feature, W feature closed or in backlog, V no assignee)` with zero
counts left out, followed by `Key: "Target Version ≠ feature" = retarget, split
or keep?` when that label appears. At most 10 owner lines, then "+N more
owner(s)" (their epics are under "Left out"). Nothing flagged → no reply at
all. The flags are informational: they never change the top post's colour,
risk line or action count.

### 6. Return the result

Return the section dict (with `kind`: `refinement` or `release-health`) to the
caller. Run on its own (for example in a DM), reply with
`chai_report.build_report(None, [section], left_out, mentions, epic_gaps=gaps)`:
post its `top_post_text`, then each string of its `replies` in the thread.

Release testing (EC/RC validation after branch cut, decision 0017) is out of scope.

## Missing data

| Missing | Result |
|---------|--------|
| Release not scheduled, an anchor not placeable, or phase not in progress | No section; returned as missing with the reason |
| Any fetch of the section incomplete | Section left out (`<fetch> incomplete: <problem>`); nothing computed from it |
| A field value still shortened (not read whole) | That fetch is incomplete (for example `<KEY>: <field> value shortened`); left out as above |
| Features did not fit on one rank-ordered page | Cut line left out; scope and risk still reported |
| Velocity | Scope only; capacity and cut line left out; feature checks still run |
| A remaining sprint's availability | That sprint not adjusted, labelled "not PTO-adjusted" |
| Open-epic fetch incomplete | No epic flags; "Epic Target Version and QA Contact checks" left out with the reason |
| An epic's parent not readable | That epic skips the Target Version and parent status checks; left out with the reason |

Never fill a gap from memory or by estimating.
