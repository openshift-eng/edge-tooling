---
name: two-node:tna-tracker
description: Track TNA (Two-Node Arbiter) cluster-etcd-operator PRs with OCPBUGS/OCPEDGE tickets, CI results, and delivery status
disable-model-invocation: true
allowed-tools: WebFetch, Bash, Write, Edit, Read, Glob, mcp__mcp-atlassian__jira_get_issue, mcp__mcp-atlassian__jira_search, mcp__mcp-atlassian__jira_search_fields
argument-hint: "[PR numbers or 'all'] [--output <file>] [--diff]"
---

# TNA Ticket Tracker

Generate a status report for **TNA (Two-Node Arbiter)** PRs in `openshift/cluster-etcd-operator`.

TNA ships via OCP payload (container image), not RHEL RPM — there are no RHEL z-stream tickets.
`arbiter` PRs → OCPBUGS/OCPEDGE tickets → CI validation

| Aspect | Value |
|--------|-------|
| Upstream repo | `openshift/cluster-etcd-operator` |
| PR keyword | `arbiter` |
| CI job pattern | `two-node-arbiter-*` |
| Ticket projects | OCPBUGS + OCPEDGE |
| Delivery | OCP payload (container image) |

## Helper Script

This command uses a helper script for all deterministic operations. The script is at:
`${SCRIPTS_DIR}/ticket_tracker_helper.py`

Available subcommands:
- `parse-args <arguments>` — Parse input into `{mode, topologies, pr_numbers, output_file, diff}`
- `validate-state <pr_data_json>` — Check ticket state staleness against PR merge date
- `group-prs <prs_json>` — Group PRs sharing a ticket reference
- `format-report <report_data_json>` — Generate the full markdown report from structured data
- `diff-data <previous.json> <current.json>` — Compare two saved data files for changes
- `history-filename <history_dir>` — Get next available report filename
- `latest-data-file <history_dir>` — Find most recent saved data JSON

All subcommands accept/return JSON. Use Bash to call them.
Pass `-` as the argument to read JSON from stdin (avoids ARG_MAX limits with large payloads):
```bash
echo '<large_json>' | python3 "${SCRIPTS_DIR}/ticket_tracker_helper.py" format-report -
```

## Workspace Discovery

Before starting, discover the workspace layout to determine where to save report history.

1. **Find workspace root**:
   ```bash
   WORKSPACE="$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")"
   ```

2. **Set history directory**:
   ```bash
   HISTORY_DIR="$WORKSPACE/.claude/skills/ticket-tracker/history"
   mkdir -p "$HISTORY_DIR"
   ```

## Input Formats

```
/two-node:tna-tracker                     # All recent arbiter PRs
/two-node:tna-tracker 1487 1481           # Specific PRs
/two-node:tna-tracker all --diff          # Compare against previous report
/two-node:tna-tracker --output report.md  # Save to file
```

## Instructions

### Step 1: Parse Arguments

```bash
python3 "${SCRIPTS_DIR}/ticket_tracker_helper.py" parse-args tna $ARGUMENTS
```

Returns JSON with `mode` ("specific" or "all"), `topologies` (will be `["tna"]`), `pr_numbers`, `output_file`, and `diff`.

### Step 2: Identify PRs

If mode is "specific": Use the `pr_numbers` from Step 1.

If mode is "all": Fetch recent arbiter PRs:
```
WebFetch https://github.com/openshift/cluster-etcd-operator/pulls?q=is%3Apr+arbiter+(is%3Aopen+OR+is%3Aclosed)+sort%3Acreated-desc
```

Extract PR numbers, titles, authors, status (open/merged/closed), and dates.
Tag each PR with `"topology": "tna"` and `"repo": "openshift/cluster-etcd-operator"`.

### Step 3: For each PR, extract ticket references

```
WebFetch https://github.com/openshift/cluster-etcd-operator/pull/<PR-number>
```
Extract both `OCPBUGS-*` and `OCPEDGE-*` references from the PR title and description.

### Step 4: Group PRs by ticket

```bash
echo '<prs_json>' | python3 "${SCRIPTS_DIR}/ticket_tracker_helper.py" group-prs -
```

Input: `[{"number": 1487, "ticket_refs": ["OCPEDGE-2097"], "topology": "tna"}, ...]`
Output: groups of PRs sharing ticket references. Use this to avoid duplicate Jira lookups.

### Step 5: Get ticket details

For each unique ticket reference:

**OCPBUGS tickets:**
```
mcp__mcp-atlassian__jira_get_issue(issue_key="OCPBUGS-XXXXX", fields="summary,status,priority,assignee,issuelinks,issuetype")
```

**OCPEDGE tickets:**
```
mcp__mcp-atlassian__jira_get_issue(issue_key="OCPEDGE-XXXXX", fields="summary,status,priority,assignee,issuelinks,issuetype")
```

Note any linked/duplicate tickets from issue links. Record the ticket `project` ("OCPBUGS" or "OCPEDGE") in the data.

### Step 6: Validate ticket state

For each PR, run state validation:
```bash
python3 "${SCRIPTS_DIR}/ticket_tracker_helper.py" validate-state '{"pr_status": "Merged", "merge_date": "2026-02-04", "ticket_status": "Done", "ticket_project": "OCPEDGE"}'
```

Set `ticket_project` to the ticket's project (`"OCPBUGS"` or `"OCPEDGE"`).

Returns `null` if valid, or a dict with `severity`, `label`, `message`, `expected_states`, and `days_since_merge`. Attach the result as `state_validation` on the PR data.

### Step 7: Check CI job status

```
WebFetch https://prow.ci.openshift.org/?repo=openshift%2Fcluster-etcd-operator&job=*two-node-arbiter*
```

Key jobs: `e2e-metal-ovn-two-node-arbiter`, `e2e-metal-ovn-two-node-arbiter-techpreview`, `e2e-metal-ovn-two-node-arbiter-upgrade`, `e2e-agent-ovn-two-node-arbiter`

Also check PR descriptions and ticket comments for Prow job links.

### Step 8: Compare with previous report (if --diff)

```bash
python3 "${SCRIPTS_DIR}/ticket_tracker_helper.py" latest-data-file "$HISTORY_DIR"
```

If a previous data file exists, you'll use it in Step 9 after assembling the current data.

### Step 9: Assemble data and generate the report

Build the report data JSON with this structure:
```json
{
  "generated_at": "YYYY-MM-DD HH:MM UTC",
  "topologies": ["tna"],
  "prs": [
    {
      "number": 1487,
      "topology": "tna",
      "repo": "openshift/cluster-etcd-operator",
      "title": "...",
      "author": "...",
      "status": "Merged",
      "date": "2026-02-04",
      "ticket": {
        "key": "OCPEDGE-2097",
        "project": "OCPEDGE",
        "summary": "...",
        "status": "Done",
        "priority": "Major",
        "assignee": "...",
        "qa_contact": null,
        "linked_tickets": []
      },
      "rhel_tickets": [],
      "ci_status": [],
      "state_validation": null
    }
  ],
  "ci_jobs": {
    "tna": [
      {"job": "...", "result": "Pass", "date": "...", "link": "..."}
    ]
  },
  "diff": null
}
```

**Key schema notes:**
- `rhel_tickets` is always `[]` for TNA PRs
- The `ticket` field has a `project` discriminator ("OCPBUGS" or "OCPEDGE")

If `--diff` was requested and a previous data file exists, compute the diff:
```bash
python3 "${SCRIPTS_DIR}/ticket_tracker_helper.py" diff-data "$PREVIOUS_DATA_FILE" /dev/stdin <<< '<current_data_json>'
```
Set the `diff` field in the report data to the result.

Then generate the report:
```bash
echo '<report_data_json>' | python3 "${SCRIPTS_DIR}/ticket_tracker_helper.py" format-report -
```

### Step 10: Save the report

Get the next filename:
```bash
python3 "${SCRIPTS_DIR}/ticket_tracker_helper.py" history-filename "$HISTORY_DIR"
```

Save **both** files to the history directory:
- `report-YYYY-MM-DD.md` — the markdown report
- `report-YYYY-MM-DD.json` — the structured data (same basename, `.json` extension) for future `--diff` runs

If `--output <file>` was specified, also write the markdown report to that path.

**Display the full report** to the user regardless of whether `--output` is used.

## Parallelization

- Fetch all PR pages from GitHub in parallel where possible
- Fetch all OCPBUGS/OCPEDGE tickets in parallel

## Notes

- Only include PRs that have a ticket reference (OCPBUGS or OCPEDGE) — skip PRs without references unless they are Open/Draft
- When run with `all`, focus on PRs from the last ~6 months to keep the report relevant
- If a PR shares a ticket with another PR, group them together
- Always save to history even if `--output` is not specified, so future `--diff` runs have a baseline
- TNA has no RHEL ticket pipeline — never search for or display RHEL tickets
