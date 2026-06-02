---
name: microshift-release:pre-check
argument-hint: [Z|X|Y|RC|EC|nightly] [version|time-range...] [--verbose]
description: Check OCP release schedule, verify availability, evaluate z-stream need, or check nightly build gaps
user-invocable: true
allowed-tools: Bash, mcp__jira__jira_search, mcp__atlassian__searchJiraIssuesUsingJql
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
| `ATLASSIAN_API_TOKEN` | ART Jira queries, advisory report, OCPBUGS check | No — scripts degrade gracefully, but advisory/CVE/OCPBUGS analysis is skipped |
| `ATLASSIAN_EMAIL` | ART Jira queries, advisory report, OCPBUGS check | No — same as above |
| `GITLAB_API_TOKEN` | Advisory report for 4.20+ (shipment MR data) | No — advisory skipped for 4.20+ without it |

## Arguments

- `release_type` (optional): One or more of `Z`, `X`, `Y`, `RC`, `EC`, `nightly` (case-insensitive). If omitted, defaults to `Z`.
- `version` (optional): Specific version (e.g., `4.19.27`) or minor stream (e.g., `4.21`)
- `time-range` (optional): Natural language time range instead of explicit versions. Detected by keywords like:
  - `today`, `tomorrow`
  - `this week`, `next week`
  - `next 3 days`, `next 7 days`
  - `this month`
- `--verbose` (optional): Show extra detail (tables for xyz, NVR/nightly names for nightly, next versions for EC/RC).

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

1. **Convert the time range** to concrete dates (`date_from`, `date_to`) based on today's date:
   - `today` → today only
   - `tomorrow` → tomorrow only
   - `this week` → today through end of current week (Sunday)
   - `next week` → next Monday through next Sunday
   - `next N days` → today through N days from now
   - `this month` → today through end of current month
   - For any other natural language range, compute the appropriate date window

2. **Query ART Jira** using the Jira MCP tool (`mcp__jira__jira_search` or `mcp__atlassian__searchJiraIssuesUsingJql`):

   ```text
   JQL: project = ART AND issuetype = Story AND summary ~ "Release 4." AND status = "In Progress" AND duedate >= "{date_from}" AND duedate <= "{date_to}" ORDER BY duedate ASC
   ```

   Use `cloudId: "redhat.atlassian.net"` for the Atlassian MCP tool.

3. **Extract versions** from ticket summaries. ART release tickets use the format `"Release X.Y.Z [YYYY-Mon-DD]"` (e.g., `"Release 4.21.18 [2026-Jun-02]"`). Extract the `X.Y.Z` version from each matching ticket.

4. **Filter to 4.14+** only (MicroShift GA'd at 4.14 — older versions have no MicroShift images).

5. **Pass the resolved versions** as explicit arguments to the script in Step 3.

If no ART tickets are found in the date range, report "No OCP releases scheduled in {range}."

### Step 3: Run the Script

Map each release type to the corresponding `precheck.sh` subcommand and run via Bash:

| Release Type | Command |
|---|---|
| `Z`, `X`, `Y` (default) | `bash ${SCRIPTS_DIR}/precheck.sh xyz [versions...]` |
| `nightly` | `bash ${SCRIPTS_DIR}/precheck.sh nightly [version]` |
| `EC` | `bash ${SCRIPTS_DIR}/precheck.sh ecrc EC [version]` |
| `RC` | `bash ${SCRIPTS_DIR}/precheck.sh ecrc RC [version]` |

**IMPORTANT**: Only append `--verbose` to the command if the user explicitly passed `--verbose` in their arguments. Do NOT add it by default.

Stderr contains progress messages — only display it if the script exits non-zero.

**Multiple types** (e.g., `nightly EC RC`): Run each command as a separate Bash call in parallel.

### Step 4: Display Output

Display the script output **verbatim** — do not reformat, add tables, or change the layout. The scripts produce deterministic pre-formatted text. Do NOT add any commentary, explanation, or summary after the output.

**OCPBUGS follow-up**: If any version shows OCPBUGS in the output (e.g., `1 OCPBUGS`), automatically re-run the command with `--verbose` to list the specific bugs. Only do this once — do not re-run if `--verbose` was already passed.

### Step 5: Handle Errors

If the script exits non-zero, display stderr and suggest:

- VPN not connected → connect to VPN (Brew requires it)
- Missing env vars → set `ATLASSIAN_API_TOKEN`, `ATLASSIAN_EMAIL`, `GITLAB_API_TOKEN` (for 4.20+)

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

## Notes

- Read-only — does NOT create tickets or modify external state
- Scripts support `--json` for raw JSON output when called directly (e.g., `bash ${SCRIPTS_DIR}/precheck.sh xyz 4.21.10 --json`)
- `--verbose` works for all types: detailed tables for xyz, NVR/nightly names for nightly, next versions for EC/RC
- Jira enrichment is optional (scripts handle gracefully without credentials)
- VPN required for Brew and errata access
