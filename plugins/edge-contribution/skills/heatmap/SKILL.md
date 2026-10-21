---
name: heatmap
description: Show the whole OCP-Edge Eng/QE team's cross-workstream contribution as a terminal allocation view — a people×workstream grid shaded by contribution amount (grey→green, log scale) with allocation signals (over/under/narrow flags) — for a quarter or date range. For team-level means and data quality, see summary --show-sources; for those means as raw numbers, see export.
allowed-tools:
  - Bash
  - Read
  - Write
  - AskUserQuestion
  - mcp__mcp-atlassian__jira_search
user-invocable: true
---

# Manager Team Heatmap

Report how the OCP-Edge Eng/QE team spread across the six workstreams (SNO, TNA,
TNF, LVMS, USHIFT, TOPO) plus cross-cutting work (SHARED) in a reporting period.
Output is a terminal **allocation view** — a people×workstream grid where shade
(grey→green, log scale) encodes contribution amount.

The output is the grid and its two legends. For the team-level means and the
data-quality block, use `/edge-contribution:summary --show-sources`, which
reports them as *people per workstream* and *workstreams per person*. The same
two numbers are exported as `mean_people_per_workstream` and
`mean_workstreams_per_person` by `/edge-contribution:export` (`metrics.csv`).

## Prerequisites

- `JIRA_USERNAME` and `JIRA_API_TOKEN` exported (same credentials the Atlassian
  MCP uses). The collectors hit `redhat.atlassian.net` REST directly.
- `gh` authenticated (`gh auth status`) with read access to
  `openshift-eng/edge-context` (the roster is fetched via `gh`) and for PR
  collection.
- Python 3.9+ with `requests` available.

## Arguments

- `--quarter <YYYYQn>` — e.g. `2026Q2`. Or use an explicit range:
- `--from <YYYY-MM-DD> --to <YYYY-MM-DD>`.
- `--ascii` — use ASCII-only glyphs (`. : * + #`) instead of Unicode block
  chars; flag glyphs become `^` and `v`.
- `--no-color` — disable ANSI color codes (text format only).

If neither `--quarter` nor a `--from/--to` pair is given, ask the user which
period to report on with AskUserQuestion.

## Steps

1. **Follow the shared pipeline** (steps 1-4 in
   [`references/pipeline.md`](../../references/pipeline.md)). The heatmap skill
   does not use `--refresh`, so you can skip parsing that flag.

2. **Render the allocation view.**

   `PLUGIN_ROOT` is `${CLAUDE_PLUGIN_ROOT}` (this plugin's directory).

   Render to stdout with `--no-color`. The grid goes to stdout; the `--output`
   flag no longer exists.

   ```bash
   python3 "$PLUGIN_ROOT/bin/report.py" --view manager \
     --members-file "$WORKDIR/roster.json" \
     --activity "$WORKDIR/jira_activity.json" \
     --activity "$WORKDIR/github_activity.json" \
     --period "$PERIOD" --format text --no-color
   ```

   Pass any `--ascii` flags the user supplied.

3. **Present the result — the rendered output and nothing else.**

   Paste `report.py`'s stdout verbatim in a fenced code block. That is the grid,
   the scale legend, and the flag legend — `_manager_text` stops there by
   design.

   **Do not add anything.** No "what to look at" section, no reading off the
   ▲/▼/narrow flags, no scores, no unattributed count, no data-quality notes,
   no commentary on who is over-allocated or which workstreams look thin, no
   note about which period or data was used. The grid says all of it, and a
   prose retelling invites the model to editorialize about named people. The
   user reads the grid. Answer follow-up questions if asked; volunteer nothing.

## See also

- **`summary`** — the executive summary with team-level means, per-member
  totals, data quality/exclusions, and (with `--show-sources`) where every
  number came from.
- **`export`** — the underlying data as four CSV files for external analysis,
  including `mean_people_per_workstream` and `mean_workstreams_per_person` in
  `metrics.csv`.

## Notes

- Contribution counts are shaded on a log scale (grey→green: `· ░ ▒ ▓ █` or
  ASCII `. : * + #`) so the full range (1 to grid_max) is perceptually balanced.
- Allocation signals (▲/▼/narrow) are relative to the team median, not external
  capacity targets. Members with zero activity are excluded from the median.
- The **SHARED** column (cross-cutting CI/tooling/docs work: `openshift/release`,
  `openshift-eng/edge-tooling`, `openshift-eng/edge-context`,
  `openshift/openshift-docs`, `openshift/enhancements`) contributes to a member's
  total workload but is deliberately excluded from the workstreams-touched
  count — that count stays "N of 6".
- Jira activity is collected from `OCPEDGE`, `USHIFT`, and `OCPBUGS` (assignee
  and QA contact), plus `OCPSTRAT` roles. Comments are **not** collected — Jira
  Cloud hides comment author emails, so the match could never fire and the
  query scanned every issue in the project once per member for zero records.
- The canonical six workstreams and their Jira component/GitHub repo mappings
  live in `bin/workstream_map.py`. Attribution uses a first-hit-wins chain:
  - Jira: own component → parent epic's component → project key
  - GitHub: Jira keys from PR title/body → repo → SHARED repos
- For data quality and exclusion details, see the `summary` skill.
- Activity files are `{"activities": [...], "collector_meta": {...}}`. A bare
  JSON list is the older format and still loads, but its `collector_meta`
  counters read as 0 — re-collect if you need them.
