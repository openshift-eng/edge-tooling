# Release Planning Risk Assessment — Method

How every figure in a release-planning report is computed, what thresholds are used and why, and what the method cannot tell you. The report's "How the numbers are computed" table is generated from `run-checks.py` and links here; this page carries the rationale.

All arithmetic lives in `plugins/edge-scrum/bin/run-checks.py`. The analysis sub-agent writes narrative only and may not introduce a number that is not in `checks.json`.

## Inputs

| Input | Source | Notes |
|---|---|---|
| Features / Initiatives | OCPSTRAT, label `ocpedge-plan` or `microshift`, Target Version = `openshift-{VERSION}` | Fetched in PM rank order (`ORDER BY Rank ASC`); the position is persisted as `rank` |
| Epics | Parent Link → feature | Target Version and Fix Version are read for scope filtering |
| Stories / Spikes / Tasks / Bugs | `parent in (epics)` | `updated` date and Sprint field are read as activity evidence |
| Roster | `.roster.json` | `sp_target` per member; law 09 default is 8 SP/sprint |
| Sprints left | `sprints.json` | Active + future sprints up to **pencils down**, not branch cut |

## Scope filtering

Before anything is counted, the tool decides which work belongs to this release.

**Epics are excluded** when they are already `Dev Complete` or `Closed` (law 04: docs/QE/CI stories may still be open, but that is not feature-dev capacity before pencils down), or when their Target Version or Fix Version is set and does not match the release. A version matches when it names the release as a whole token: `5.1`, `5.1.0`, `5.1.z`, `openshift-5.1`, `MicroShift 5.1`; `5.10` does not match `5.1`. Excluded epics and their open SP are listed in the report's process-gaps data so nothing disappears silently.

If an excluded epic's version tag disagrees with its parent feature, fix the tag in Jira and re-run; the tool never aliases versions. A version alias (`5.1 = 4.22`) would encode a mapping the data itself can contradict — the same tag can sit on both finished and carry-over work — so the correct fix is the field, not a CLI flag. Open SP under a mistagged epic is surfaced in the verdict line as "pending version confirmation" so it is not lost while the tag is corrected.

**Features are classified active or dormant.** Law 05 says a feature in `New` is not a commitment. A feature is **dormant** only if:

```text
status == New
AND ( no epics
      OR epics have no non-bug stories
      OR no non-bug story is In Progress / Review / done,
         sits in a sprint inside the release window,
         or was updated within the last 30 days )
```

Everything else is active, including any feature past `New` regardless of evidence — the workflow state is authoritative (law 12). Assignment alone is *not* evidence: an assignee set at creation and never touched does not mean work is happening. Each dormant feature carries the reason it was classified that way.

Risk checks run on **active** features only. Dormant features are a scope decision for PM, not a risk.

## Figures

### Capacity

```text
capacity = Σ over roster members ( sp_target × sprints_left )
```

Every roster member counts, including those with nothing assigned. People who carry work but are not on the roster contribute **zero** capacity and are listed as roster drift; their work still counts as scope. The two sides of the comparison must cover the same team, otherwise the gap is meaningless — a person missing from the roster inflates scope without adding capacity, and an idle roster member is capacity the tool would otherwise ignore.

Not yet modelled: PTO, public holidays, the partial current sprint, and bug-fix load. Law 09 requires capacity to be adjusted for the first two; until that is done, treat capacity as an upper bound.

### Scope (pointed)

```text
scope = Σ story points of open, non-bug stories
        under active features that pass the data-quality gate
```

Bugs are always 0 SP (law 14). Features that fail the gate (no epics, or no stories) have no numeric scope and are reported as data-quality failures.

### Hidden scope

The team points just-in-time, so unpointed stories are real work with no number attached. Folding them into the SP total would fabricate points; ignoring them hides load. The tool reports them separately, with an estimate:

```text
hidden = unpointed_open_stories × SP per story
SP per story: 25th / 50th / 75th percentile (inclusive method) of closed pointed stories in the data set
              (fallback 2 / 3 / 5 if fewer than 4 closed pointed stories)
```

Shown as a range, never added to the pointed total. The number of unpointed stories that are already assigned is reported too, because those are the ones most likely to be pulled into a sprint.

### Gap

```text
gap = capacity − scope            (negative = overcommitted)
gap incl. hidden = capacity − scope − hidden estimate   (shown as a range)
```

### Cut line

Active features are listed in PM rank order with cumulative remaining SP. The first feature whose cumulative total crosses capacity is marked *partial*; everything after it *over*. This answers "what comes out" using an ordering the team already maintains. If the Jira rank is stale, so is this table — it does not make scope decisions, it shows where the current ordering meets capacity.

### Person over target

```text
over  if assigned open SP  >  sp_target × sprints_left
```

Unpointed assigned stories are shown beside the SP figure and never summed into it. A person who looks fine on points but carries several unpointed stories is probably full.

### Feature timeline

```text
velocity_on_feature = Σ roster contributors ( sp_target × share )
    share = that person's open SP on this feature ÷ their total open SP across all active features
    non-roster contributors add no velocity (consistent with capacity)
sprints_needed = remaining SP ÷ velocity_on_feature
HIGH  if sprints_needed > sprints_left
NO_CONTRIBUTORS  if remaining SP > 0 and nobody is assigned
```

This is a proportional model: a contributor's target is split across their features in proportion to where their points are. It answers "at the current allocation, does this finish". It does **not** answer "is this feature too big" — see sizing.

### Sizing (informational)

```text
dedicated_sprints = total pointed SP (done + open, non-bug)
                    ÷ Σ contributors' sp_target
Undersized  if dedicated_sprints > max sprints for the T-shirt size
            or (size XS/S and ≥ 4 epics and ≥ 50 SP)
```

| Size | Max sprints (law 05) |
|---|---|
| XS | 2 |
| S | 3 |
| M | 4 |
| L | 5 |
| XL | 5 |

This compares the feature's own size against its label, assuming its contributors worked on it exclusively. It is deliberately independent of how busy those people are elsewhere. Sizing is displayed but **not counted** in the composite risk, because its earlier form was derived from the same load figures as the timeline and capacity signals and would count one overloaded person three times.

### Composite risk

```text
signals = count of { timeline HIGH/NO_CONTRIBUTORS,
                     any contributor over target,
                     SPOF or unassigned stories,
                     unassigned Blocker/Critical bug,
                     data-quality FAIL }
HIGH ≥ 3 · MEDIUM = 2 · LOW ≤ 1
```

Overall report risk is HIGH if any feature is HIGH, MEDIUM if any is MEDIUM, else LOW. Thresholds are a convention, not a calibration; they were chosen so that a single signal never escalates a feature on its own.

### Data-quality gate

The gate is **completion-aware** (already-done stories are removed before the ratio, so a nearly-finished feature is not penalised for the estimates on work it has shipped) and **importance-weighted** (an open Blocker/Critical story with no estimate always WARNs; a couple of low-priority unpointed leftovers on a feature that is clearly underway are treated as noise). Story size cannot weight unpointed items — they have no points by definition — so priority stands in for importance. Constants: important priorities = `Blocker`, `Critical`; small unpointed remainder = `2`.

Conditions are evaluated **top to bottom; the first that matches wins.** "Open" excludes done stories (`Closed`, and `Verified` for OCPBUGS); bugs are never counted as stories here.

| # | Condition | Result | Reason shown |
|---|---|---|---|
| 1 | No epics, but one or more were excluded (done / other-release) | FAIL | `all N epics excluded (<first reason>)` |
| 2 | No epics and none excluded | FAIL | `no epics created` |
| 3 | Epics exist but no non-bug stories | FAIL | `epics have no stories` |
| 4 | All non-bug stories are done | PASS | `all N stories complete` |
| 5 | No open story is unpointed | PASS | — |
| 6 | ≥ 1 open Blocker/Critical story is unpointed | WARN | `N blocker/critical open stories lack an estimate` |
| 7 | ≥ 50 % of open stories are pointed | PASS | — |
| 8 | ≤ 2 low-priority open stories unpointed **and** ≥ 1 story done | PASS | `nearly done (X% complete), only N low-priority open stories unpointed` |
| 9 | Otherwise | WARN | `N of M open stories unestimated` |

After a PASS from rows 4–8, one overlay applies: if any of the feature's live epics has no non-bug stories, the feature is downgraded to **WARN** — `N of M epics have no stories` — because it is only partially refined.

FAIL features are excluded from capacity and timeline arithmetic (there is nothing to sum). A feature that FAILs only because its epics were all excluded (row 1) is finished or mis-targeted, not un-refined — the report says so rather than "no epics created", and lists these features separately in the process gaps. WARN features are included; their unpointed stories feed the hidden-scope estimate.

## Known limitations

- Capacity ignores PTO, holidays, partial sprints and bug-fix time. Treat it as an upper bound.
- Timeline and capacity signals both respond to the same overloaded contributors, so an overcommitted person raises the risk of every feature they touch. That is often correct, but it is not two independent findings.
- The cut line depends on the Jira rank being current.
- Hidden-scope estimates use the team's own closed-story distribution, which may not represent the unpointed stories.
- Story sprint membership and `updated` are only as good as Jira hygiene. A feature can be classified dormant because nobody touched the tickets, not because nobody worked on it.
- Roster drift corrupts every capacity figure. Reconcile `.roster.json` with actual assignees before trusting the gap. Until then, non-roster assignees add scope but no capacity and no velocity, so a feature they own alone shows `NO_CONTRIBUTORS`.
- Closed bugs are ignored for bug load; only open Blocker/Critical bugs without an assignee count as a signal.

## Changing thresholds

Thresholds live as named constants at the top of `run-checks.py` (`SIZE_TO_MAX_SPRINTS`, `DEFAULT_SP_TARGET`, `DEFAULT_ACTIVITY_DAYS`, `FALLBACK_SP_RANGE`) and as CLI flags (`--activity-days`). Change them there, add a test, and update this page — the report's method table is generated and will follow automatically.
