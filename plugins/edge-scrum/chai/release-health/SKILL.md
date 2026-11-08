---
name: release-health
description: Read-only coordinator skill that reports team-level health for one release in its current phase — refinement and test-plan spike gaps in the refinement sprint; capacity vs scope, cut line and features at risk from development through pre-branch-cut
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
   50 issues.
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

**Features keep PM rank.** Fetch features once with `(<JQL>) ORDER BY Rank ASC`,
`max_results: 50`, and adapt with `"order": "rank"`. If that page has the
`Found N issues (showing N):` header, the order is the PM rank. If it shows only
the first 50 ("more match"), fetch the
features again with the key recipe above: scope and risk can still be computed,
but the **cut line is left out** (`release_health(..., rank_available=False)`),
since key order is not rank.

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

The release-health `--refinement` checks for refinement and test-plan spikes.

1. **Features in Refinement**, paged:
   `project = OCPSTRAT AND issuetype in (Feature, Initiative) AND labels in ("ocpedge-plan", "microshift") AND "Target Version" = "openshift-X.Y" AND status = "Refinement" AND (resolution is EMPTY OR resolution not in (Duplicate, Obsolete))`
   — fields `summary, status, issuetype, priority, assignee, fixVersions, labels,
   customfield_10795, customfield_10470, customfield_10473, customfield_10475`.
   None → the section says "no features in Refinement" and has no gaps.
2. **Epics**, paged, in batches of 50 feature keys:
   `project in (OCPEDGE, USHIFT) AND "Parent Link" in (<keys>)` — fields
   `summary, status, assignee, labels, parent, updated, fixVersions,
   customfield_10028, customfield_10795, customfield_10855, customfield_10018,
   customfield_10470`. Run `transform-epics.py`.
3. **Refinement-sprint spikes**, paged:
   `project in (OCPEDGE, USHIFT) AND issuetype = Spike AND sprint = <refinement sprint id>`
   — fields `summary, status, assignee, resolution`. `query_jira` cannot return
   issue links, so call `get_jira_issue` on each spike and attach its links with
   `attach_issue_links` (needed to see spikes that block a child epic instead of
   the feature). If it returns any problem (a failed call, or no readable
   "Linked Jira Issues" section), the refinement section is left out ("spike
   links incomplete"): a missed link would report a false missing spike. A
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
5. Write `{"refinement_sprint_closed": <refinement sprint state is closed>}` as
   `sprints.json`, then run `transform-features.py`, and `transform-spikes.py`
   with `--input <refinement-sprint spikes> --linked-spikes <all linked spikes>
   --features-file --epics-file --sprints-file`.
6. `chai_report.refinement_gaps(features, spikes, section, refinement_sprint_closed)`
   applies the release-health refinement-spike rules (first match wins: Closed →
   fine; missing but refined via epics → fine; missing → 🔴, owner SME; open but
   not in the refinement sprint → 🟡 move it, owner SME; open in the refinement
   sprint → in progress) and the test-plan spike rules (skipped when the QA
   Contact is empty or `unassigned_jira`; missing or not Closed → 🟡, owner the
   QE Lead). A Closed test plan spike satisfies the check whatever its resolution.

### 4. Release-health mode (development, pencils-down sprint, pre-branch-cut)

Capacity vs scope, cut line and features at risk, with release-planning's
`run-checks.py`.

1. **Features**, rank-ordered (see Jira paging):
   `project = OCPSTRAT AND issuetype in (Feature, Initiative) AND labels in ("ocpedge-plan", "microshift") AND "Target Version" = "openshift-X.Y" AND (resolution is EMPTY OR resolution not in (Duplicate, Obsolete))`
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
   capacity, cut line and features at risk as left out (its roster-sum fallback
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
8. `chai_report.release_health(checks, section, capacity, rank_available,
   velocity_reason)` (`velocity_reason` from `usable_velocity`, None when usable): scope
   vs capacity, features below the cut line (owner: SME), features at risk
   (composite HIGH or timeline HIGH; owner: SME). Per-person capacity rows and
   `ABOVE_GUIDELINE` flags are dropped; roster drift becomes a count with its SP,
   listed as left out. No prompt about roster drift.

### 5. Return the result

Return the section dict (with `kind`: `refinement` or `release-health`) to the
caller. Run on its own (for example in a DM), reply with
`chai_report.build_report(None, [section], left_out, mentions)`.

Release testing (EC/RC validation after branch cut, decision 0017) is out of scope.

## Missing data

| Missing | Result |
|---------|--------|
| Release not scheduled, an anchor not placeable, or phase not in progress | No section; returned as missing with the reason |
| Any fetch of the section incomplete | Section left out (`<fetch> incomplete: <problem>`); nothing computed from it |
| Features did not fit on one rank-ordered page | Cut line left out; scope and risk still reported |
| Velocity | Scope only; capacity, cut line and features at risk left out |
| A remaining sprint's availability | That sprint not adjusted, labelled "not PTO-adjusted" |

Never fill a gap from memory or by estimating.
