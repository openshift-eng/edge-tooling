---
name: export
description: Export OCP-Edge cross-workstream contribution data for a quarter or date range as four CSV files — raw activities, member×workstream matrix, team metrics, and data quality counts.
allowed-tools:
  - Bash
  - Read
  - Write
  - AskUserQuestion
user-invocable: true
---

# Manager Data Export

Export the OCP-Edge Eng/QE team's cross-workstream contribution data as four CSV
files for external analysis, pivot tables, or archival. The export covers the
same data the `heatmap` and `summary` skills render, but in a structured tabular
format for tools like Excel, R, or Python.

## Output files

All four files are written to `--output-dir` (defaults to
`/tmp/edge-contribution-$PERIOD/export-$PERIOD`):

| File | Contents |
|------|----------|
| `activities.csv` | One row per activity record — the raw collated data. Columns: `member`, `source` (jira/github), `kind`, `workstream`, `attribution_source`, `repo`, `issue_key`, `url`, `ts`, `unattributed_reason` |
| `matrix.csv` | People × workstream counts. Columns: `member`, `SNO`, `TNA`, `TNF`, `LVMS`, `USHIFT`, `TOPO`, `SHARED`, `TOTAL`, `workstreams_touched`, `over`, `under`, `narrow` |
| `metrics.csv` | Team scalars and per-workstream totals/contributors. Columns: `metric`, `value` |
| `data-quality.csv` | Exclusion and unattributed counts. Columns: `category`, `label`, `count` |

## Prerequisites

Same as `heatmap` — see the plugin README for Jira credentials, `gh` auth, and
Python requirements.

## Arguments

- `--quarter <YYYYQn>` — e.g. `2026Q2`. Or use an explicit range:
- `--from <YYYY-MM-DD> --to <YYYY-MM-DD>`.
- `--output-dir <path>` — where to write the four CSV files. Defaults to
  `/tmp/edge-contribution-$PERIOD/export-$PERIOD`.
- `--refresh` — re-collect activity even if cached files exist.

If neither `--quarter` nor a `--from/--to` pair is given, ask the user which
period to report on with AskUserQuestion.

## Steps

1. **Follow the shared pipeline** (steps 1-4 in
   [`references/pipeline.md`](../../references/pipeline.md)). Parse the
   `--refresh` flag if the user supplied it.

2. **Set the output directory:**

   ```bash
   OUTPUT_DIR="${OUTPUT_DIR:-$WORKDIR/export-$PERIOD}"
   ```

3. **Export the data:**

   ```bash
   python3 "$PLUGIN_ROOT/bin/export_csv.py" \
     --members-file "$WORKDIR/roster.json" \
     --activity "$WORKDIR/jira_activity.json" \
     --activity "$WORKDIR/github_activity.json" \
     --period "$PERIOD" --output-dir "$OUTPUT_DIR"
   ```

4. **Present the result — the four file paths and what's in them, nothing else.**

   List the four paths (`activities.csv`, `matrix.csv`, `metrics.csv`,
   `data-quality.csv`) with a one-line description per file, exactly as
   documented in the "Output files" table above.

   **Do not add anything.** No commentary on the numbers, no reading off totals,
   no notes about which period was used. The user asked for the data; give them
   the paths. Answer follow-up questions if asked; volunteer nothing.

## See also

- **`heatmap`** — the grid alone, instantly.
- **`summary`** — the executive summary with team-level means, and (with
  `--show-sources`) where every number came from.

## Where the data comes from

**Do NOT filter `activities.csv` by the `ts` column to isolate a quarter.** GitHub
PR review collection uses `updated:<window>` rather than `created:<window>`,
because the GitHub search API has no review-date qualifier. Every GitHub record
is timestamped with the PR's creation date (authored and reviewed alike), so a
review done in-window of an older PR carries a pre-window timestamp. In live
2026Q3, 145 of 1284 GitHub records (11%) have a `ts` outside the quarter. They
are real in-window contributions and ARE counted. Filtering by `ts` drops those
rows incorrectly.
