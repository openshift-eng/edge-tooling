---
name: microshift-release:pre-check
argument-hint: [Z|X|Y|RC|EC|nightly] [version|time-range...] [--verbose]
description: Check OCP release schedule, verify availability, evaluate z-stream need, or check nightly build gaps
user-invocable: true
allowed-tools: Bash, mcp__productpages__browse_schedule, mcp__productpages__list_entities, mcp__atlassian__getJiraIssue, mcp__atlassian__searchJiraIssuesUsingJql
---

# microshift-release:pre-check

## Synopsis

```bash
/microshift-release:pre-check [release_type] [version|time-range...] [--verbose]
```

## Description

MicroShift ships as a layered product on top of OCP. Every time OCP releases a new version (z-stream, EC, RC, or nightly), the MicroShift team must evaluate whether to participate — checking for CVEs, verifying RPM builds exist in Brew, and deciding whether to ask ART to create artifacts.

This command automates that evaluation (Phase 0 of the release process). It checks lifecycle status, OCP payload availability, advisory CVEs, nightly build gaps, and EC/RC readiness — then outputs a clear action per version: OK, SKIP, ASK ART, NEEDS REVIEW, or ALREADY RELEASED.

When a time range is provided (e.g., "this week"), it queries Red Hat Product Pages for OCP versions scheduled in that period and evaluates each one.

## Prerequisites

| Requirement | Needed for | Mandatory? |
|---|---|---|
| VPN | Brew RPM checks (nightly, EC/RC), advisory report | Yes for nightly/ecrc — xyz degrades gracefully (skips advisory, 90-day rule) |
| Atlassian MCP | OCPBUGS enrichment, ART ticket queries | Yes — required to analyze OCPBUGS resolution and release action |
| `GITLAB_API_TOKEN` | Advisory report for 4.20+ (shipment MR data) | No — advisory skipped for 4.20+ without it |
| Product Pages MCP | Time range lookups (e.g., "this week") | Only when using time ranges — not needed for explicit versions |

## Arguments

- `release_type` (optional): One or more of `Z`, `X`, `Y`, `RC`, `EC`, `nightly` (case-insensitive). If omitted, defaults to `Z`.
- `version` (optional): Specific version (e.g., `4.19.27`) or minor stream (e.g., `4.21`)
- `time-range` (optional): Natural language time range instead of explicit versions. Detected by keywords like:
  - `today`, `tomorrow`
  - `this week`, `next week`, `last week`
  - `next 3 days`, `next 7 days`
  - `April`, `this month`
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

### Step 2: Resolve Versions from Product Pages (when time range is detected)

If a time range is present instead of explicit versions, query Product Pages to find OCP z-stream versions scheduled in that period:

1. **Convert the time range** to concrete dates (date_from, date_to) based on today's date
2. **Find active OCP z-release entities**: Use `mcp__productpages__list_entities` with `public_parent_id=146` (OCP product), `kind="release"`, `shortname="%z"`, `is_maintained=True`. Filter results to 4.14+ only (MicroShift GA'd at 4.14 — older z-release entities have no MicroShift images).
3. **For each z-release entity**: Call `mcp__productpages__browse_schedule` and find tasks where:
   - `flags` contains `"ga"` (these are the "X.Y.Z in Fast Channel" milestones)
   - `date_finish` falls within [date_from, date_to]
   - Extract the version from the task name (e.g., "4.21.10 in Fast Channel" → `4.21.10`)
4. **Collect all matching versions** across all streams and pass them to the xyz script

If the `mcp__productpages__list_entities` tool is not available (MCP not loaded), stop and show this message verbatim:

````text
The Product Pages MCP is required for time-range lookups but is not enabled in this session.

To enable it, run this command:

```bash
claude mcp add productpages -s user --transport http https://productpages.redhat.com/mcp --header "X-MCP-Realm: urn:mcp:realm:private-core"
```

Then restart Claude Code and re-run the command.

Alternatively, pass explicit versions instead of a time range:
```
  /microshift-release:pre-check 4.19.X 4.20.X 4.21.X
```
````

If no versions found in the schedule, report "No OCP releases scheduled in \<range\>."

### Step 3: Query ART Tickets via MCP

Before running the script, query ART Jira for in-progress release tickets so the script can show ART ticket status in the Release Schedule table.

1. Call `mcp__atlassian__searchJiraIssuesUsingJql` with:
   - `cloudId`: `"redhat.atlassian.net"`
   - `jql`: `project = ART AND summary ~ "Release" AND status = "In Progress" ORDER BY duedate ASC`
   - `fields`: `["summary", "status", "duedate"]`
   - `maxResults`: `50`
2. From the results, build a JSON array:

   ```json
   [{"key": "ART-XXXXX", "summary": "Release 4.21.18 [2026-Jun-03]", "status": "In Progress", "due_date": "2026-06-03"}]
   ```

3. Write the JSON to a temp file and set `ART_TICKETS_JSON` env var:

   ```bash
   echo '<json>' > /tmp/art_tickets.json
   ```

If `mcp__atlassian__searchJiraIssuesUsingJql` is not available, skip this step — the script degrades gracefully (shows `None` for ART tickets).

### Step 4: Run the Script

Map each release type to the corresponding `precheck.sh` subcommand and run via Bash:

| Release Type | Command |
|---|---|
| `Z`, `X`, `Y` (default) | `ART_TICKETS_JSON=/tmp/art_tickets.json bash ${SCRIPTS_DIR}/precheck.sh xyz [versions...]` |
| `nightly` | `bash ${SCRIPTS_DIR}/precheck.sh nightly [version]` |
| `EC` | `ART_TICKETS_JSON=/tmp/art_tickets.json bash ${SCRIPTS_DIR}/precheck.sh ecrc EC [version]` |
| `RC` | `ART_TICKETS_JSON=/tmp/art_tickets.json bash ${SCRIPTS_DIR}/precheck.sh ecrc RC [version]` |

**IMPORTANT**: Only append `--verbose` to the command if the user explicitly passed `--verbose` in their arguments. Do NOT add it by default.

Stderr contains progress messages — only display it if the script exits non-zero.

**Multiple types** (e.g., `nightly EC RC`): Run each command as a separate Bash call in parallel.

### Step 5: Display Output

Display the script output **verbatim** — do not reformat, add tables, or change the layout. The scripts produce deterministic pre-formatted text. Do NOT add any commentary, explanation, or summary after the output.

**OCPBUGS follow-up**: If any version shows OCPBUGS in the output (e.g., `1 OCPBUGS`), automatically re-run the command with `--verbose` to list the specific bugs. Only do this once — do not re-run if `--verbose` was already passed.

### Step 6: Enrich OCPBUGS via MCP

After displaying the output (including any `--verbose` re-run), if any OCPBUGS appeared in the results:

1. **Collect OCPBUGS keys**: Extract all unique `OCPBUGS-XXXXX` keys from the output (they appear in the Resolved OCPBUGS table or the one-line summaries).
2. **Fetch each bug via MCP**: For each unique key, call `mcp__atlassian__getJiraIssue` with:
   - `cloudId`: `"redhat.atlassian.net"`
   - `issueIdOrKey`: the OCPBUGS key (e.g., `"OCPBUGS-12345"`)
   - `fields`: `["summary", "status", "labels", "issuetype", "priority"]`
   - `responseContentFormat`: `"markdown"`
   Make all `getJiraIssue` calls **in parallel** (multiple tool calls in one message).
3. **Build enriched JSON**: For each successfully fetched bug, build a JSON object:

   ```json
   {
     "key": "OCPBUGS-12345",
     "version": "4.21",
     "summary": "<from MCP response: fields.summary>",
     "status": "<from MCP response: fields.status.name>",
     "labels": ["<from MCP response: fields.labels array>"],
     "issuetype": "<from MCP response: fields.issuetype.name>",
     "priority": "<from MCP response: fields.priority.name>"
   }
   ```

   The `version` field is the minor version (e.g., `"4.21"`) from the evaluation that referenced the bug. If a bug appears in multiple versions, include one entry per version.
4. **Render enrichment table**: Pipe the JSON array through the enrichment script:

   ```bash
   echo '<json_array>' | bash ${SCRIPTS_DIR}/precheck.sh enrich
   ```

5. **Display the enrichment output** after the main precheck output. This shows real summaries, statuses, release actions (release-required/release-not-required/needs-review), and updated recommendations.

If `mcp__atlassian__getJiraIssue` is not available, skip enrichment and note that the Atlassian MCP is required for OCPBUGS analysis.

### Step 7: Handle Errors

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

## Product Pages Reference

- OCP product entity ID: **146** (from `search_entities("OpenShift Container Platform", kind="product")` — hardcoded to skip one API call)
- Z-release entities: `openshift-X.Y.z` (e.g., `openshift-4.21.z`)
- Schedule tasks with `"ga"` flag = version GA dates ("X.Y.Z in Fast Channel")
- `date_finish` on ga-flagged tasks = release date

## Notes

- Read-only — does NOT create tickets or modify external state
- Scripts support `--json` for raw JSON output when called directly (e.g., `bash ${SCRIPTS_DIR}/precheck.sh xyz 4.21.10 --json`)
- `--verbose` works for all types: detailed tables for xyz, NVR/nightly names for nightly, next versions for EC/RC
- OCPBUGS enrichment uses Atlassian MCP (OAuth) — no PAT env vars needed; the script discovers bug keys from git commits, and the skill enriches them via `getJiraIssue`
- VPN required for Brew and errata access
