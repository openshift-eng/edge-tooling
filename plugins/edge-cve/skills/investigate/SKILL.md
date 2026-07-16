---
name: edge-cve:investigate
argument-hint: "[--workdir DIR] [--dry-run] [--skip-scan] [--local] [--check-repo URL --ref REF]"
description: Investigate open Black CVE Jira tickets, run govulncheck scans, and produce actionable remediation reports
user-invocable: true
allowed-tools: Skill, Bash, Read, Write, Glob, Grep, Agent
---

# edge-cve:investigate

## Synopsis

```bash
/edge-cve:investigate
/edge-cve:investigate --dry-run
/edge-cve:investigate --skip-scan
/edge-cve:investigate --local
/edge-cve:investigate --check-repo https://github.com/openshift/lvm-operator --ref release-4.18 --cve CVE-2024-99999
```

## Description

Fetches open **Black** CVE tickets from Jira using the intersection of the
`All Open CVEs` and `All Open Black CVEs` saved filters, categorizes them by
component and version, resolves repository targets, launches OpenShift
govulncheck jobs, and produces a team notification report.

For a quick one-off check outside the Jira-driven pipeline (e.g. "is this
repo/branch affected by anything, right now"), use `--check-repo` (see
[Ad-hoc single-repo check](#ad-hoc-single-repo-check) below) instead of the
full prepare/scan/finalize flow.

**Deterministic scripts** handle Jira fetch, parsing, grouping, scan target
generation, job orchestration, and report generation. **LLM agents** are used
only for:

1. Reviewing ambiguous CVE groups flagged by `group_cves.py`
2. Analyzing govulncheck results to decide if remediation is required
3. Refining remediation prompts for affected repositories

## Arguments

Parse `$ARGUMENTS` for optional flags:

| Flag | Effect |
|------|--------|
| `--workdir DIR` | Override work directory (default: `/tmp/edge-cve-workdir.<YYMMDD>`) |
| `--dry-run` | Run `prepare` and render OpenShift job manifests without applying them |
| `--skip-scan` | Skip scan launch/collection entirely; generate report from existing scan data |
| `--local` | Run the scan sequentially via podman (`scan-local`) instead of OpenShift Jobs; no cluster required |
| `--check-repo URL --ref REF` | Ad-hoc single-repo mode (see below): bypasses the whole Jira pipeline; `--cve ID` (repeatable, optional), `--ticket KEY` (repeatable, optional), `--jira-url`, `--summary`, `--component` add context |

## Prerequisites

| Requirement | Purpose |
|-------------|---------|
| `JIRA_BASE_URL` | Jira instance (default: `https://redhat.atlassian.net`) |
| `JIRA_EMAIL` or `JIRA_USERNAME` | Jira authentication |
| `JIRA_API_TOKEN` | Jira API token |
| `oc` + OpenShift login | Launch govulncheck jobs (unless `--skip-scan` or `--local`) |
| `podman` | Alternative local scan execution (`--local`), no cluster required |
| Python 3 + `requests` | Deterministic scripts |

## Jira Query

The default JQL is fixed to the Black CVE filter intersection:

```jql
filter = "All Open CVEs" AND filter = "All Open Black CVEs"
```

Do NOT broaden this query. Only Black CVEs in this intersection are in scope.

## Work Directory

Compute once at the start by running `date +%y%m%d` unless `--workdir` is
provided:

```text
/tmp/edge-cve-workdir.<YYMMDD>
```

Prescribed outputs:

| Path | Producer |
|------|----------|
| `jira/cves-raw.json` | `fetch_cves.py` |
| `jira/cves-parsed.json` | `parse_cves.py` |
| `jira/cves-grouped.json` | `group_cves.py` |
| `jira/cves-llm-review.json` | `group_cves.py` (ambiguous groups) |
| `scans/scan-targets.json` | `build_scan_targets.py` |
| `scans/govulncheck-results.json` | `collect_govulncheck_results.py` |
| `report-cve-investigation.md` | `generate_report.py` |
| `report-cve-investigation.html` | `generate_html_report.py` |
| `remediation-prompts.md` | `generate_report.py` |

## Implementation Steps

### Step 1: Prepare — Fetch, Parse, Group, Build Scan Targets

**Goal**: Deterministically collect and structure all in-scope Black CVEs.

**Actions**:

1. Compute `<WORKDIR>` from `date +%y%m%d` or use `--workdir` from arguments.
2. Run:

   ```text
   bash plugins/edge-cve/scripts/cve-investigator.sh prepare --workdir <WORKDIR>
   ```

3. Read the JSON printed by the script. Note `count`, `by_component`, and
   `go_target_count`.
4. Read `<WORKDIR>/jira/cves-llm-review.json`. If `groups` is non-empty,
   proceed to Step 1b. Otherwise skip to Step 2.

**Error handling**:

- Missing Jira credentials: show env var setup from `plugins/edge-cve/README.md` and stop.
- Zero issues returned: report "No open Black CVEs found" and stop.

### Step 1b: LLM Review of Ambiguous Groups (Conditional)

**Goal**: Confirm or adjust deterministic grouping for tickets flagged
`needs_llm_review`.

**Actions**:

1. Launch a single **foreground** Agent:

   ```text
   Agent: subagent_type=generalPurpose, prompt="Review CVE grouping for edge-cve investigation.
   Read <WORKDIR>/jira/cves-llm-review.json and <WORKDIR>/jira/cves-grouped.json.

   For each flagged group, decide whether tickets represent the same underlying
   vulnerability across versions/components. If groups should be merged or split,
   write an updated <WORKDIR>/jira/cves-grouped-reviewed.json with the same schema
   as cves-grouped.json.

   If no changes are needed, copy cves-grouped.json to cves-grouped-reviewed.json
   unchanged.

   Do NOT invent CVE IDs or repositories. Only reorganize existing ticket data.
   Reply DONE when cves-grouped-reviewed.json is written."
   ```

2. Rebuild scan targets from the reviewed grouping:

   ```text
   python3 plugins/edge-cve/scripts/build_scan_targets.py \
     --workdir <WORKDIR> \
     --input <WORKDIR>/jira/cves-grouped-reviewed.json
   ```

### Step 2: Run govulncheck Scans

Skip this step when `--skip-scan` is set.

**Actions**:

1. If `--local` (podman, no cluster required — run one target at a time):

   ```text
   bash plugins/edge-cve/scripts/cve-investigator.sh scan-local --workdir <WORKDIR>
   ```

   This writes `scans/govulncheck-results.json` directly; skip to Step 3
   (no separate collect step for local runs).

2. Else if `--dry-run` (OpenShift, render manifests without applying):

   ```text
   bash plugins/edge-cve/scripts/cve-investigator.sh scan --workdir <WORKDIR> --dry-run
   ```

   Report how many jobs would be created and stop before Step 3.

3. Otherwise (OpenShift):

   ```text
   bash plugins/edge-cve/scripts/cve-investigator.sh scan --workdir <WORKDIR>
   bash plugins/edge-cve/scripts/cve-investigator.sh collect --workdir <WORKDIR>
   ```

4. If collection times out, note partial results and continue.

### Step 3: Analyze govulncheck Results (LLM)

**Goal**: Determine which scan results are truly actionable.

**Actions**:

1. Read `<WORKDIR>/scans/govulncheck-results.json`.
2. For each result where `affected` is true, `scan_incomplete` is true (the
   scan was signal-killed, typically OOM - see below), or `scan_exit_code` is
   non-zero with `finding_count` > 0, launch **foreground** Agents in a single
   message (one per affected/incomplete target):

   ```text
   Agent: subagent_type=generalPurpose, prompt="Analyze govulncheck output for CVE actionability.
   Target: <TARGET_ID>
   Read the result entry in <WORKDIR>/scans/govulncheck-results.json.
   Read related tickets in <WORKDIR>/jira/cves-parsed.json.

   If scan_incomplete is true, the scan container was killed (typically OOM,
   scan_exit_code 137) before govulncheck finished - do NOT interpret the
   empty/partial findings as evidence of anything. Verdict must be
   "inconclusive", with the recommended action being to re-run
   run_govulncheck_podman.sh/run_govulncheck_jobs.sh with a higher --memory.

   Otherwise decide: affected_and_actionable | affected_but_transitive | false_positive | inconclusive
   Explain using matched findings and ticket context.

   Save a short analysis to <WORKDIR>/scans/analysis-<TARGET_ID>.txt including:
   - verdict
   - evidence (module/path/CVE)
   - recommended action (bump dep, vendor fix, not applicable)

   Reply DONE <analysis-file-path> only."
   ```

3. Do NOT use LLM for tickets already marked `not_affected` with exit code 0.

### Step 4: Finalize — Generate Reports

**IMPORTANT**: Mandatory even when scans are skipped or partial.

```text
bash plugins/edge-cve/scripts/cve-investigator.sh finalize --workdir <WORKDIR>
```

This writes both `report-cve-investigation.md` (team notification markdown,
covering every ticket) and `report-cve-investigation.html` (browsable,
filterable, grouped by component → version, with govulncheck status per
ticket). The HTML is deliberately scoped to components listed in
`config/component-repos.json` - the Black CVE filter spans hundreds of
components across the whole org, so anything not in that config is dropped
before rendering (count reported on stdout as `dropped_unmapped_components`).
Tickets whose Jira
Security Level or labels indicate they're private/restricted (see
`lib.cve_extract.is_private_ticket`) are rendered in the HTML with **only** a
link back to the Jira ticket - no CVE ID, summary, or scan findings, since
any of those could leak details of an embargoed vulnerability. Do not
work around this redaction (e.g. by reading the raw JSON to describe a
private ticket's contents in chat) unless the user explicitly asks you to
after being made aware it's marked private.

### Step 5: Report Completion

Display:

1. Path to `report-cve-investigation.md` and `report-cve-investigation.html`
2. Count of affected vs not-affected tickets (and how many were redacted as private)
3. Path to `remediation-prompts.md` for actionable items
4. Link to Jira filter for manual verification

## Ad-hoc single-repo check

When `--check-repo URL --ref REF` is given, skip Steps 1-5 entirely and run
this instead:

```text
bash plugins/edge-cve/scripts/cve-investigator.sh check-repo \
  --repo-url <URL> --ref <REF> \
  [--cve <CVE-ID> ...] [--ticket <KEY> ...] \
  [--jira-url <URL>] [--summary <TEXT>] [--component <NAME>] \
  [--workdir <DIR>] [--memory <MEM>] [--cpus <N>] [--timeout <SECONDS>]
```

This deterministically (no LLM call needed for the base result):

1. Clones `<URL>` at `<REF>` and runs `govulncheck` in a disposable podman
   container (same hardening as `scan-local`: named container, wall-clock
   timeout, cleanup on exit, shared module/toolchain cache).
2. If `--cve` is given, checks specifically for those CVE(s); if omitted,
   reports any known vulnerability govulncheck finds at that ref.
3. Computes a `verdict` (`affected` | `not_affected` | `inconclusive` - the
   last one for OOM-killed/incomplete scans) and prints a JSON object with a
   `suggested_agent_prompt` field: a ready-to-use remediation prompt built
   from a fixed template and the scan's own matched findings when
   `action_required` is true, or `null` when the repo isn't affected.

Display the printed JSON (particularly `verdict` and `suggested_agent_prompt`)
directly to the user. If `verdict` is `affected` and the user wants it fixed
now, offer to launch a **foreground** Agent with the `suggested_agent_prompt`
text as its prompt - it's stated in the base command's own words, so review it
first rather than editorializing on top of it.

## Examples

### Full investigation

```bash
/edge-cve:investigate
```

### Dry-run (no cluster changes)

```bash
/edge-cve:investigate --dry-run
```

### Re-generate report from existing scans

```bash
/edge-cve:investigate --skip-scan
```

### Ad-hoc check: is this repo/branch affected by a specific CVE

```bash
/edge-cve:investigate --check-repo https://github.com/openshift/lvm-operator --ref release-4.18 --cve CVE-2024-99999
```

### Ad-hoc check: any known vulnerability at this ref (no specific CVE)

```bash
/edge-cve:investigate --check-repo https://github.com/openshift/microshift --ref release-4.19
```

## Related Skills

- **microshift-dev:golang-cve-analyzer** — Single-ticket golang/Brew CVE check
- **microshift-ci:doctor** — Similar prepare/analyze/finalize orchestration pattern

## Notes

- Extend `plugins/edge-cve/config/component-repos.json` when new components need default repo mapping.
- Scan refs come from each ticket's Jira versions via `version_ref_template`
  (e.g. `4.18` → `release-4.18`). Do not add `main`/`master` to
  `version_ref_fallbacks` for versioned components - tip-of-tree captures far
  more than the ticket is asking about. Tickets with a repo but no resolvable
  release ref are skipped (`no_git_ref_resolved`), not pointed at `main`.
- Non-Go components are listed in `scan-targets.json` under `skipped_targets` and are not scanned by govulncheck.
- The investigation is read-only against Jira; it does not transition or comment on tickets.
