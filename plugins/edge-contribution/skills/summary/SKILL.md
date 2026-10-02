---
name: summary
description: Executive summary of OCP-Edge cross-workstream contribution for a quarter or date range — team activity, per-workstream volume, per-member totals, and data quality — in text or markdown format. Reports raw measurements with no judgments.
allowed-tools:
  - Bash
  - Read
  - Write
  - AskUserQuestion
user-invocable: true
---

# Manager Executive Summary

An executive summary of how the OCP-Edge Eng/QE team contributed across the six
workstreams (SNO, TNA, TNF, LVMS, USHIFT, TOPO) plus cross-cutting work (SHARED)
in a reporting period. The summary reports raw measurements with no judgments —
no "over", "under", "narrow" flags, no risk labels. The manager applies their own
judgment. The default output contains four sections:

- **TEAM** — total contributions, median per person, people per workstream (mean),
  workstreams per person (active members only).
- **WORKSTREAMS** — volume, contributor count, and largest contributor with their
  percentage share, sorted by volume descending.
- **PEOPLE** — total contributions, workstreams touched (N of 6), and largest
  workstream with percentage share, sorted by total descending.
- **DATA HEALTH** — unattributed record count and breakdown by category (Jira
  tickets vs PRs, unmapped repos vs missing components).

Add `--show-sources` to include three additional sections describing where the
numbers came from: what was counted (sources, projects, collection method), how
each item was attributed (first-hit-wins chain with counts), and what was
excluded (managers, personal repos, full unattributed breakdowns).

Output formats are `text` (default, a plain-text report) and `markdown` (tables
and headings for pasting into Slack, email, or a Google Doc).

## Prerequisites

Same as `heatmap` — see the plugin README for Jira credentials, `gh` auth, and
Python requirements.

## Arguments

- `--quarter <YYYYQn>` — e.g. `2026Q2`. Or use an explicit range:
- `--from <YYYY-MM-DD> --to <YYYY-MM-DD>`.
- `--format <text|markdown>` — defaults to `text`.
- `--show-sources` — append the collection and attribution detail (what was
  counted, how each item was attributed, full exclusion lists).
- `--include-all` — include excluded members (managers on the roster by role
  title who are not IC contributors).
- `--refresh` — re-collect activity even if cached files exist.

If neither `--quarter` nor a `--from/--to` pair is given, ask the user which
period to report on with AskUserQuestion.

## Steps

1. **Follow the shared pipeline** (steps 1-4 in
   [`references/pipeline.md`](../../references/pipeline.md)). Parse the
   `--refresh` flag if the user supplied it.

2. **Render the executive summary:**

   ```bash
   FORMAT="${FORMAT:-text}"   # default to text if not set
   SHOW_SOURCES_FLAG=""
   if [[ "$SHOW_SOURCES" == "true" ]]; then
     SHOW_SOURCES_FLAG="--show-sources"
   fi
   python3 "$PLUGIN_ROOT/bin/report.py" --view summary \
     --members-file "$WORKDIR/roster.json" \
     --activity "$WORKDIR/jira_activity.json" \
     --activity "$WORKDIR/github_activity.json" \
     --period "$PERIOD" --format "$FORMAT" $SHOW_SOURCES_FLAG
   ```

   Pass `--include-all` if the user supplied it.

3. **Present the result — the rendered output and nothing else.**

   Paste `report.py`'s stdout verbatim in a fenced code block (for `text`) or as
   markdown (for `markdown`).

   **Do not add anything.** No "what to look at" section, no reading a share
   percentage back as a risk, no commentary on which workstreams look thin or
   who is carrying too much, no note about which period or data was used. The
   summary reports raw measurements — it deliberately carries no flags, labels,
   or thresholds, and adding that judgement in prose puts back exactly what the
   output was built to leave out. The user reads the summary. Answer follow-up
   questions if asked; volunteer nothing.

## See also

- **`heatmap`** — the grid alone, instantly.
- **`export`** — the underlying data as four CSV files.

## Source-disclosure sections

The `--show-sources` flag appends three sections after DATA HEALTH:

- **WHAT WAS COUNTED** — record totals by source (Jira/GitHub) and kind
  (assignee/qa/pr_authored/pr_reviewed), with collection-method notes (which
  projects, which orgs, the GitHub review-date caveat).
- **HOW EACH ITEM WAS ATTRIBUTED** — the first-hit-wins chain (component → repo
  → project → shared → parent), with a count and percentage for each step and
  the unattributed total.
- **WHAT WAS EXCLUDED** — what was deliberately left out (managers,
  personal-namespace repos, unattributed items), with counts, repo names, and
  specific examples. The full unattributed breakdown appears here, including the
  `openshift-eng/two-node-toolbox` note ("mixed arbiter/fencing; not mapped by
  design").

GitHub PR review collection uses `updated:<window>` rather than
`created:<window>`, because the GitHub search API has no review-date qualifier.
Every GitHub record is timestamped with the PR's creation date (authored and
reviewed alike), so a review done in-window of an older PR carries a pre-window
timestamp. In live 2026Q3, 145 of 1284 GitHub records (11%) have a `ts` outside
the quarter. They are real in-window contributions and ARE counted by the
summary and the grid. The WHAT WAS COUNTED section reports this count when
non-zero.
