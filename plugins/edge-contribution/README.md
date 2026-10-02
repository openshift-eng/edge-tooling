# edge-contribution

Cross-workstream contribution reporting for the OpenShift Edge team. Three
manager skills for a quarter or date range from the same underlying data:

- **`heatmap` skill:** a terminal allocation view — a people×workstream
  grid shaded by contribution amount (grey→green, log scale) with allocation
  signals (over/under/narrow flags), instantly. The output is that grid and its
  two legends. For the team-level means and the data-quality block, see
  `summary --show-sources`; for those means as raw numbers, see `export`
  (`metrics.csv`).
- **`summary` skill:** an executive summary — team activity, per-workstream
  volume/contributors, per-member totals and workstreams touched, and data
  quality. Reports raw measurements with no judgments. Add `--show-sources` for
  collection/attribution
  diagnostics. Output in `text` or `markdown` for pasting into Slack, email, or
  a Google Doc.
- **`export` skill:** the underlying data as four CSV files for
  external analysis, pivot tables, or archival.

For the individual-contributor view of this same data — one engineer's
workstreams-touched count "N of 6" — see `/edge-ic:workstreams` in the `edge-ic`
plugin. It runs these same collectors over a single member.

The six workstreams are **SNO, TNA, TNF, LVMS, USHIFT, TOPO**, plus a **SHARED**
column for cross-cutting CI/tooling/docs work. Activity is drawn from Jira
(OCPEDGE/USHIFT/OCPBUGS assignee + QA-contact, OCPSTRAT roles) and GitHub
(authored + reviewed PRs), and each item is attributed to a workstream via a
first-hit-wins chain described below.

## Install

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install edge-contribution
```

## Prerequisites

- **Jira credentials:** export `JIRA_USERNAME` and `JIRA_API_TOKEN` (the same
  credentials the Atlassian MCP uses). The collectors call
  `redhat.atlassian.net` REST directly — no MCP at runtime.
- **GitHub CLI:** `gh` installed and authenticated (`gh auth status`), with read
  access to `openshift-eng/edge-context` — the roster is fetched via `gh`, so no
  local checkout is needed.
- **Python 3.9+** with `requests` available.

## Usage

Invoke the skills from Claude Code:

```text
/edge-contribution:heatmap --quarter 2026Q2
/edge-contribution:summary --quarter 2026Q2 --format markdown
/edge-contribution:export --quarter 2026Q2 --output-dir /tmp/2026Q2-export
```

All three cover the full Eng/QE roster and accept `--include-all`. `heatmap`
also accepts `--ascii` and `--no-color`. `summary` and `export` accept
`--refresh` to re-collect activity even if cached files exist.

### Export output files

The `export` skill writes four CSV files:

| File | Contents |
|------|----------|
| `activities.csv` | One row per activity record — the raw collated data. Columns: `member`, `source` (jira/github), `kind`, `workstream`, `attribution_source`, `repo`, `issue_key`, `url`, `ts`, `unattributed_reason` |
| `matrix.csv` | People × workstream counts. Columns: `member`, `SNO`, `TNA`, `TNF`, `LVMS`, `USHIFT`, `TOPO`, `SHARED`, `TOTAL`, `workstreams_touched`, `over`, `under`, `narrow` |
| `metrics.csv` | Team scalars and per-workstream totals/contributors. Columns: `metric`, `value` |
| `data-quality.csv` | Exclusion and unattributed counts. Columns: `category`, `label`, `count` |

### Running the pipeline directly

The skills orchestrate standalone scripts under `bin/`. You can run them by hand:

```bash
# 1. roster (Eng/QE only) fetched from edge-context via gh
python3 bin/load_context.py --output roster.json

# 2. collect activity for the window
python3 bin/collect_jira.py   --members-file roster.json --quarter 2026Q2 \
  --output jira_activity.json
python3 bin/collect_github.py --members-file roster.json --quarter 2026Q2 \
  --output github_activity.json

# 3. render a report
python3 bin/report.py --view manager --members-file roster.json \
  --activity jira_activity.json --activity github_activity.json \
  --period 2026Q2 --format text

python3 bin/report.py --view summary --members-file roster.json \
  --activity jira_activity.json --activity github_activity.json \
  --period 2026Q2 --format markdown

# the single-member view, consumed by the edge-ic plugin's `workstreams` skill
python3 bin/report.py --view ic --member jdoe@redhat.com \
  --activity jira_activity.json --activity github_activity.json \
  --period 2026Q2 --format text

# 4. export as CSV
python3 bin/export_csv.py --members-file roster.json \
  --activity jira_activity.json --activity github_activity.json \
  --period 2026Q2 --output-dir /tmp/export-2026Q2
```

## Metrics

Defined precisely in [`references/metrics.md`](references/metrics.md). In short,
from a binary "did member *m* contribute to workstream *w*?" matrix:

- **Workstreams touched** (per member) = distinct canonical workstreams the
  member contributed to, "of 6" (SHARED is deliberately excluded so the count
  stays "N of 6").
- **Mean people per workstream** = `total_touches / 6` (SHARED excluded), where
  `total_touches` is the number of 1s in the matrix.
- **Mean workstreams per person** = `total_touches / active_count`, averaged
  over *active* members and shown with the active/total roster count.

Allocation signals (from the weighted `counts` matrix, not the binary one):

- **Over-allocated** — members whose total is >2× the team median.
- **Under-allocated** — members whose total is <0.5× the team median and >0.
- **Narrow** — members where a single workstream is >70% of their total.

Items that cannot be attributed to any workstream are reported as
**unattributed** — counted separately, never silently dropped. For where each
number came from and for data quality, see the `summary` skill.

## Workstream Attribution

Activity items are mapped to workstreams via a **first-hit-wins chain**:

### Jira attribution (assignee/QA-contact/OCPSTRAT roles)

Assignee and QA-contact activity is searched across `OCPEDGE`, `USHIFT`, and
`OCPBUGS`; OCPSTRAT roles are searched separately because they answer a
different question (SME/assignee on strategic items, cf 10475).

**Comments are deliberately not collected.** Jira Cloud omits `emailAddress`
from comment authors, so matching a roster member by email could never fire —
the query returned zero records while scanning every issue in the project once
per member. Pinned by `TestCommentCollectionIsGone` in `test_collect_jira.py`.
Re-adding it requires matching on `accountId`, not email.

1. Issue's own `components` field → workstream acronym (e.g., "SNO" → SNO)
2. Parent epic's component (batched lookup via `key in (...)` JQL)
3. Jira project key (e.g., USHIFT-6337 → MicroShift/USHIFT)

### GitHub attribution (authored/reviewed PRs)

1. **Excluded repos** — personal-namespace repos (owner not in the org allowlist:
   `openshift`, `openshift-eng`, `microshift-io`, `openshift-metal3`, `metal3-io`,
   `containers`, `clusterlabs`, `ovn-kubernetes`, `opendatahub-io`, `kubevirt`)
   are dropped entirely and do not appear even as unattributed.

   **A Jira key does not override this.** Work counts only when it lands in a
   Red Hat product or engineering org. A PR opened against a branch in
   someone's own repo is not counted even when its title names a ticket — the
   exclusion deliberately runs *before* Jira-key resolution, and
   `test_jira_key_does_not_rescue_a_personal_repo_pr` pins that order.

   The allowlist is hand-kept, so a legitimate org missing from it would be
   dropped silently — the data-quality block therefore names every dropped
   repo, not just a total. That block is available in
   `summary --show-sources`.
2. **Jira keys from PR title/body** → resolve each key via the Jira chain above,
   emit one activity per distinct workstream.
3. **GitHub repo direct mapping** — repos that unambiguously belong to a single
   workstream (e.g., `openshift/lvm-operator` → LVMS, `openshift/microshift` →
   USHIFT, `openshift/oc-tnf` → TNF).
4. **SHARED repos** — cross-cutting CI/tooling/docs repos credited to the SHARED
   column: `openshift/release`, `openshift-eng/edge-tooling`,
   `openshift-eng/edge-context`, `openshift/openshift-docs`,
   `openshift/enhancements`.
5. **Unattributed** — if none of the above match, the PR is kept with
   `workstream=None` and appears in the data-quality footnote.

### Activity file format

Both collectors write the same envelope:

```json
{
  "activities": [ { "member": "...", "workstream": "...", "..." : "..." } ],
  "collector_meta": {
    "excluded_personal_repo_prs": 99,
    "excluded_personal_repos": { "jeff-roche/roundhouse": 38 }
  }
}
```

`collector_meta` exists for data-quality counters that no record can carry,
because the thing being counted never produced a record — a PR dropped for
living in a personal namespace is counted but leaves nothing behind. A counter
is either a plain total or a `{label: count}` breakdown; when several activity
files are loaded, totals add and breakdowns merge label by label. `report.py`
also accepts a bare JSON list (the pre-envelope format); those files just report
`collector_meta` counters as 0.

Unattributed Jira records carry an `unattributed_reason` separating
`no_component_no_parent` (fix the ticket) from `parent_also_empty` (fix the
epic), so the data-quality block says which one to go fix.

### Standing exclusions

Two deliberate exclusions (restored with `--include-all` on the `heatmap` skill):

- **Repo:** `openshift-eng/two-node-toolbox` is **deliberately NOT mapped** to
  any workstream. That repo covers BOTH arbiter (TNA) and fencing (TNF)
  topologies, so mapping it either way would mis-credit work. This was an
  explicit product decision — do not "fix" it by adding a mapping.

## Architecture

```text
workstream_map.py   internal SNO/TNA/TNF/LVMS/USHIFT/TOPO ↔ Jira component map
load_context.py     edge-context roster (fetched via gh) → roster.json (Eng/QE)
collect_jira.py     Jira REST → jira_activity.json      (assignee/QA/ocpstrat)
collect_github.py   gh api graphql (batched) → github_activity.json (pr_authored/pr_reviewed)
report.py           activity + roster → contribution matrix → metrics → render
metrics.py          matrix → workstreams touched / team means / allocation signals
render.py           report → text | markdown
_common.py          Jira config/auth, HTTP client (pagination/retry), date helpers
```

`workstream_map`, `load_context`, `metrics`, `render`, and `report` are pure and
side-effect-free; all Jira/GitHub/filesystem I/O is isolated in the collectors
and `_common.py`, with the HTTP and `gh` layers injected so tests stay hermetic.
GitHub queries use batched GraphQL (aliased searches, 6 members per request) to
stay well under the 5000 points/hour budget; body-level `RATE_LIMITED` errors
are detected structurally and retried with bounded exponential backoff. If a
chunk request fails, it splits in half and retries each half. Jira workstream
resolution batches all unique keys extracted from PRs into JQL `key in (...)`
queries. Jira HTTP 429 responses retry with the same backoff policy. Other
authentication and command failures are surfaced immediately.

## Development

Built test-first (TDD). Run the suite:

```bash
python3 -m pytest plugins/edge-contribution/bin/tests
```

Optional style/type gate (see `requirements-dev.txt`):

```bash
black --check bin && ruff check bin && mypy bin
```

Every module has happy-path, failure, and edge/boundary tests. See the plan and
`references/` for the design rationale and the workstream/component map.
