# edge-contribution — known gaps

Open work on the allocation heatmap, worst first. Every item lists where the
problem lives and how to tell it is fixed.

Measured against live 2026Q3 data: 2159 activity records, 249 still unattributed.

## P1 — changes what the heatmap tells you

### 1. Jira collection only covers OCPEDGE and OCPSTRAT — DONE

Assignee and QA-contact searches now span `_ACTIVITY_PROJECTS` =
`(OCPEDGE, USHIFT, OCPBUGS)`. OCPSTRAT stays on its own query (cf 10475).

Comment collection was **removed**, not widened — see the "Deliberate" section.

- [x] Decide the project set: `USHIFT` and `OCPBUGS`.
- [x] Widen the assignee/QA JQL builders to `project in (...)` off a single
      constant (`collect_jira._ACTIVITY_PROJECTS`).
- [x] Check the comment scope query first. It turned out to be dead code, and
      widening it would have cost ~106k issue fetches per run. Deleted instead.
- [x] Verify against live 2026Q3: Jira records **436 → 876**.

| | before | after |
|---|---|---|
| USHIFT | 29 | 305 |
| TNF | 108 | 199 |
| LVMS | 74 | 101 |
| TNA | 13 | 22 |
| unattributed | 101 | 137 |

`attribution_source == "project"` records now appear with `kind` in
`assignee`/`qa` (181 of them), not just PR kinds. The +36 unattributed are
OCPBUGS tickets with no mappable component — real work, correctly disclosed
rather than invisible.

### 2. Manager view does not show the activity-kind mix

Requirement from the design interview was "all kinds count 1, but show the mix"
so reviews are distinguishable from owned work. The manager grid shows totals
only. `build_workstream_activities` in `report.py` already computes per-kind
counts, but only the IC view renders them.

- [ ] Pick a presentation that does not wreck the grid. A per-member authored vs.
      reviewed split in the TOTAL column is probably enough.
- [ ] Verify: a member who only reviews is visually distinct from one who only
      authors, at the same total.

### 3. Failed Jira batches degrade attribution silently

`collect_jira.py:160` and `collect_github.py:474`, `:496` all swallow
`CollectorError` with `continue`. A dead batch leaves those keys unattributed,
with no warning and no count. It also inflates `tickets_parent_also_empty`,
because an unfetchable parent is indistinguishable from a component-less one.

- [ ] Count failed batches and the keys they covered.
- [ ] Surface the count through `collector_meta` into the data-quality block.
- [ ] Verify: force a batch to fail in a test, assert the count is reported
      rather than absorbed.

## P2 — silent data loss

### 4. GitHub search truncates past 1000 results

`collect_github.py:288` only checks node-count completeness when
`issueCount <= 1000`, because GitHub caps search results there. Above the cap,
PRs are dropped with no error at all.

- [ ] Detect `issueCount > 1000` and split that slot's window (e.g. per-month)
      until each sub-window is under the cap.
- [ ] Verify: a slot reporting >1000 either returns complete results or fails
      loudly. It must not return a truncated set quietly.

### 5. Personal-repo detection is a hardcoded org allowlist — DONE

`workstream_map.py:88` lists the known orgs; `:232` treats everything else as a
personal namespace and drops it. A legitimate new org was silently excluded.

Audited against live 2026Q3: 101 dropped PRs across 12 repos. 82 were one
person's three hobby repos (correctly dropped). One real miss — `kubevirt` is a
genuine org, and two [OCPBUGS-100031](https://issues.redhat.com/browse/OCPBUGS-100031)
PRs were being thrown away. They now attribute to TNF.

- [x] Add `kubevirt` to `_ALLOWED_ORGS`.
- [x] Log dropped repos by name, not just a total count
      (`collect_github.tally_excluded_repos` → `collector_meta.excluded_personal_repos`).
- [x] Verify: the data-quality block names which repos were dropped, so an
      unexpected org is visible instead of inferred.

Resolved: `tnf-preset` (4 PRs across `pablofontanilla/` and `fonta-rh/`, 2
citing [OCPBUGS-104449](https://issues.redhat.com/browse/OCPBUGS-104449)) stays
excluded. See the product rule in the "Deliberate" section below.

### 6. Jira key regex is a fixed project list

`collect_github.py:76` matches only `OCPEDGE|USHIFT|OCPBUGS|OCPSTRAT|MGMT|ETCD|RHEL|CNV`.
Keys from any other project in PR text are not seen.

- [ ] Either widen to a generic `[A-Z]+-\d+` with a rejection list, or accept
      the current list and document it as deliberate.

### 7. GitHub review timestamps reflect PR creation, not review date

`collect_github.py:142` searches `created:<window>` for authored PRs but
`updated:<window>` for reviewed PRs, because GitHub search has no review-date
qualifier. Every GitHub record is timestamped with the PR's creation date
(`collect_github.py:159`), authored and reviewed alike. A review done in-window
of an older PR therefore carries a pre-window timestamp. In live 2026Q3, 145 of
1284 GitHub records (11%) have a `ts` outside the quarter. They are real
in-window contributions and ARE counted.

- [ ] Document in the export skill: do NOT filter `activities.csv` by the `ts`
      column to isolate a quarter — you would drop those 145 rows.
- [ ] Verify: run the export for 2026Q3, filter `activities.csv` to
      `source=github AND ts NOT BETWEEN '2026-07-01' AND '2026-09-30'`, and
      confirm the count matches the documented 145 (or the current live number).

## P3 — accuracy notes

### 8. `tickets_parent_also_empty` is slightly overcounted

`unattributed_reason` in `collect_jira.py` cannot separate "the epic has no
component" from "Jira would not return the epic". Both count as
`parent_also_empty` (33 in 2026Q3). Fixing this depends on item 3.

### 9. Pre-envelope activity snapshots read counters as 0

A bare JSON list still loads, but `collector_meta` counters come back 0. This is
intended backward compatibility, not a bug. Re-collect if the counters matter.

## Deliberate — do not "fix"

- **Jira comments are not collected.** Jira Cloud omits `emailAddress` from
  comment authors (verified live: author objects carry only `accountId`,
  `displayName`, `accountType`, `active`, `avatarUrls`, `self`, `timeZone`).
  The old `_is_member_comment_in_window` compared `author.emailAddress` to the
  member's email, so it never matched — 2026Q3 produced **zero** `comment`
  records while scanning 520 issues per member with full comment bodies. The
  whole path is gone. Re-adding it means resolving each member's email to an
  `accountId` first, and it must never widen past one project: at
  `OCPEDGE + USHIFT + OCPBUGS` the scan is 5,877 issues × 18 members ≈ 106k
  fetches per run. Pinned by `TestCommentCollectionIsGone`.
- **Only work in a Red Hat product or engineering org counts.** A PR opened
  against a branch in someone's own repo is not counted even when its title
  names a Jira ticket. The personal-namespace exclusion therefore runs *before*
  Jira-key attribution in `collect_github.pr_to_activities`; do not reorder the
  chain to "rescue" keyed PRs. Pinned by
  `test_jira_key_does_not_rescue_a_personal_repo_pr`.
- `openshift-eng/two-node-toolbox` is unmapped. It covers both arbiter (TNA) and
  fencing (TNF), so mapping it either way mis-credits work. Product decision.
- Allocation flags are relative to the team median, with no FTE or capacity
  baseline. A uniformly overloaded team therefore reads as balanced. Product
  decision.
