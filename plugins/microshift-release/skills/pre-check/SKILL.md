---
name: microshift-release:pre-check
argument-hint: [Z|X|Y|RC|EC|nightly] [version|time-range...]
description: Check OCP release schedule, verify availability, evaluate z-stream need, or check nightly build gaps
user-invocable: true
allowed-tools: Bash, mcp__jira__jira_search
---

# microshift-release:pre-check

## Synopsis

```bash
/microshift-release:pre-check [release_type] [version|time-range...] [--verbose]
```

## Description

MicroShift ships as a layered product on top of OCP. Every time OCP releases a new version (z-stream, EC, RC, or nightly), the MicroShift team must evaluate whether to participate — checking for CVEs, verifying RPM builds exist in Brew, and deciding whether to ask ART to create artifacts.

This command automates that evaluation (Phase 0 of the release process). It checks lifecycle status, OCP payload availability, advisory CVEs, nightly build gaps, and EC/RC readiness — then outputs a clear action per version: OK, SKIP, ASK ART, NEEDS REVIEW, or ALREADY RELEASED.

When a time range is provided (e.g., "this week"), it queries ART Jira for OCP release tickets due in that period and evaluates each one.

## Prerequisites

| Requirement | Needed for | Mandatory? |
|---|---|---|
| VPN | Brew RPM checks (nightly, EC/RC), advisory report | Yes for nightly/ecrc — xyz degrades gracefully (skips advisory, 90-day rule) |
| Jira MCP | ART ticket queries, time range lookups | Yes — required for resolving versions from time ranges |
| `JIRA_API_TOKEN` + `JIRA_USERNAME` | OCPBUGS enrichment, advisory CVE enrichment, component CVE discovery | No — script degrades gracefully (shows "Pending Jira lookup") |
| `GITLAB_API_TOKEN` | Advisory report for 4.20+ (shipment MR data) | No — advisory skipped for 4.20+ without it |

## Arguments

- `release_type` (optional): One or more of `Z`, `X`, `Y`, `RC`, `EC`, `nightly` (case-insensitive). If omitted, defaults to `Z`.
- `version` (optional): Specific version (e.g., `4.19.27`) or minor stream (e.g., `4.21`)
- `time-range` (optional): Natural language time range instead of explicit versions. Detected by keywords like:
  - `today`, `tomorrow`
  - `this week`, `next week`
  - `next 3 days`, `next 7 days`
  - `this month`

## Scripts Directory

All scripts are run relative to the repository root:

```bash
SCRIPTS_DIR=plugins/microshift-release/scripts
```

## Implementation

### Step 1: Parse Arguments

1. Identify `release_type`(s) — tokens matching `Z`, `X`, `Y`, `RC`, `EC`, `nightly` (case-insensitive)
2. Identify `version`(s) — tokens matching `X.Y` or `X.Y.Z` pattern
3. Identify `time range` — remaining tokens that are not release types, versions, or flags (e.g., "this week", "next 3 days", "tomorrow")
4. Identify `--verbose` flag
5. **Default**: If no release_type found, default to `Z` and treat version/time-range tokens accordingly

### Step 2: Resolve Versions via ART Jira (when time range is detected)

If a time range is present instead of explicit versions, query ART Jira for release tickets due in that window:

1. **Convert the time range** to concrete dates (`date_from`, `date_to`) based on today's date. **Weeks always start on Monday** (ISO 8601):
   - `today` → today only
   - `tomorrow` → tomorrow only
   - `this week` → Monday of the current ISO week through the following Sunday (if today is Sunday, it belongs to the week that started on the previous Monday)
   - `next week` → Monday after the current ISO week through the following Sunday
   - `next N days` → today through N days from now
   - `this month` → today through end of current month
   - For any other natural language range, compute the appropriate date window

2. **Query ART Jira** using `mcp__jira__jira_search`:

   ```text
   jql: project = ART AND issuetype = Story AND summary ~ "Release 4." AND duedate >= "{date_from}" AND duedate <= "{date_to}" ORDER BY duedate ASC
   ```

   Use `limit: 50` to ensure all tickets in the window are returned.

3. **Extract versions** from ticket summaries. ART release tickets use the format `"Release X.Y.Z [YYYY-Mon-DD]"` (e.g., `"Release 4.21.18 [2026-Jun-02]"`). Extract the `X.Y.Z` version from each matching ticket.

4. **Filter to 4.14+** only (MicroShift GA'd at 4.14 — older versions have no MicroShift images).

5. **Pass the resolved versions** as explicit arguments to the script in Step 4.

If no ART tickets are found in the date range, report "No OCP releases scheduled in {range}."

### Step 3: Query ART Tickets via MCP

Before running the script, query ART Jira for in-progress release tickets so the script can show ART ticket status in the Release Schedule table.

1. Call `mcp__jira__jira_search` with:
   - `jql`: `project = ART AND summary ~ "Release" AND status = "In Progress" ORDER BY duedate ASC`
   - `fields`: `summary,status,duedate`
   - `limit`: `50`
2. From the results, build a JSON array:

   ```json
   [{"key": "ART-XXXXX", "summary": "Release 4.21.18 [2026-Jun-03]", "status": "In Progress", "due_date": "2026-06-03"}]
   ```

3. Write the JSON to a temp file and set `ART_TICKETS_JSON` env var:

   ```bash
   echo '<json>' > /tmp/art_tickets.json
   ```

If `mcp__jira__jira_search` is not available, skip this step — the script degrades gracefully (shows `None` for ART tickets).

### Step 4: Run the Script

Map each release type to the corresponding `precheck.sh` subcommand and run via Bash:

| Release Type | Command |
|---|---|
| `Z`, `X`, `Y` (default) | `ART_TICKETS_JSON=/tmp/art_tickets.json bash ${SCRIPTS_DIR}/precheck.sh xyz [versions...]` |
| `nightly` | `bash ${SCRIPTS_DIR}/precheck.sh nightly [version]` |
| `EC` | `ART_TICKETS_JSON=/tmp/art_tickets.json bash ${SCRIPTS_DIR}/precheck.sh ecrc EC [version]` |
| `RC` | `ART_TICKETS_JSON=/tmp/art_tickets.json bash ${SCRIPTS_DIR}/precheck.sh ecrc RC [version]` |

Stderr contains progress messages — only display it if the script exits non-zero.

**Multiple types** (e.g., `nightly EC RC`): Run each command as a separate Bash call in parallel.

### Step 5: Display Output

The script produces two output modes:

The script outputs all analysis in a single run:

1. **One-liner summaries** — quick action per version
2. **Release Schedule** — ART ticket, due date, OCP status, lifecycle
3. **Z-Stream Evaluation** — last released, days since, commits, CVE impact, OCPBUGS count
4. **Advisory Report** — per-advisory CVE details with MicroShift impact reasons
5. **Resolved OCPBUGS** — enriched bug details (status, release action, summary)
6. **Recommendations** — combined table with: Recommendation, Version, OCP, CVEs, OCPBUGS, Last Release, Reason
7. **CVEs Requiring Release** — detail table when there are must-release CVEs

Display the **complete stdout** as **rendered markdown** (NOT as a
code block). The script outputs markdown tables — they MUST render
as formatted tables, not raw text. Every section must be visible
to the user. Do not summarize, abbreviate, or replace the output
with your own tables or commentary. Bold the Recommendation column
values in the Recommendations table.

The Recommendations table already combines summary data with
recommendation reasons — no parsing or reformatting needed.
No `--verbose` re-run or MCP enrichment steps are needed — the
script handles everything internally via `JIRA_API_TOKEN`.

### Step 6: Handle Errors

If the script exits non-zero, display stderr and suggest:

- VPN not connected → connect to VPN (Brew requires it)
- Missing env vars → set `GITLAB_API_TOKEN` (for 4.20+ advisory reports)

## Examples

```bash
/microshift-release:pre-check this week                   # OCP versions releasing this week
/microshift-release:pre-check next week                   # OCP versions releasing next week
/microshift-release:pre-check today                       # OCP versions releasing today
/microshift-release:pre-check next 3 days                 # OCP versions in next 3 days
/microshift-release:pre-check 4.21.10                     # specific version
/microshift-release:pre-check 4.20 4.21 4.22              # xyz eval for multiple streams
/microshift-release:pre-check 4.19.27 --verbose           # specific version, detailed report
/microshift-release:pre-check nightly                     # nightly gaps for all active branches
/microshift-release:pre-check EC                          # latest EC status
/microshift-release:pre-check RC                          # latest RC status
/microshift-release:pre-check nightly EC RC               # combined report
```

## Recommendation statuses

| Emoji | Status | Meaning |
|-------|--------|---------|
| 🔴 **ASK ART** | Action needed now | OCP payload ready, must release |
| ⏳ **BLOCKED** | Action decided, waiting | Need to release but OCP payload not ready yet |
| 🟡 **NEEDS REVIEW** | Human judgment needed | Unlabeled OCPBUGS, ambiguous cases |
| 🟢 **SKIP** | No action | No CVEs, no bugs, within 90 days |
| ✅ **ALREADY RELEASED** | Done | Already shipped |

## Notes

- Read-only — does NOT create tickets or modify external state
- Scripts support `--json` for raw JSON output when called directly (e.g., `bash ${SCRIPTS_DIR}/precheck.sh xyz 4.21.10 --json`)
- `--verbose` works for all types: detailed tables for xyz, NVR/nightly names for nightly, next versions for EC/RC
- OCPBUGS and CVE enrichment run inside the script via Jira REST API (`JIRA_API_TOKEN` + `JIRA_USERNAME`)
- VPN required for Brew and errata access
