---
name: two-node:tnf-tracker
description: Track TNF (Two-Node Fencing) tickets and PRs across all repos — OCPBUGS status, RHEL z-stream clones, Preliminary Testing, build info, and CI results
disable-model-invocation: true
allowed-tools: WebFetch, Bash, Write, Edit, Read, Glob, mcp__mcp-atlassian__jira_get_issue, mcp__mcp-atlassian__jira_search, mcp__mcp-atlassian__jira_search_fields
argument-hint: "[ticket keys or PR numbers or 'all'] [--output <file>] [--diff]"
---

# TNF Ticket Tracker

Generate a status report for **TNF (Two-Node Fencing)** tickets and their associated PRs.

Uses a **ticket-first** approach: searches Jira for tickets labeled `two-node-fencing`, then follows PR links back to their repos. This covers PRs across all TNF-related repos, not just `resource-agents`.

For `resource-agents` PRs, also tracks the RHEL z-stream lifecycle:
OCPBUGS ticket → downstream RHEL z-stream clones → Preliminary Testing → build info

| Aspect | Value |
|--------|-------|
| Jira label | `two-node-fencing` |
| Ticket projects | OCPBUGS, OCPEDGE |
| CI job pattern | `two-node-fencing-*` |
| RHEL z-stream | Yes (for `resource-agents` PRs) |
| Repos | Any repo with PRs linked to TNF tickets |

## Helper Script

This command uses a helper script for all deterministic operations. The script is at:
`${SCRIPTS_DIR}/ticket_tracker_helper.py`

Available subcommands:
- `parse-args <arguments>` — Parse input into `{mode, topologies, pr_numbers, output_file, diff}`
- `detect-streams <tickets_json>` — Add `stream` field to RHEL tickets from fixVersions/summary
- `check-zstream-gaps <streams_json>` — Compare streams against expected coverage
- `validate-state <pr_data_json>` — Check ticket state staleness against PR merge date
- `group-prs <prs_json>` — Group PRs sharing a ticket reference
- `format-report <report_data_json>` — Generate the full markdown report from structured data
- `diff-data <previous.json> <current.json>` — Compare two saved data files for changes
- `history-filename <history_dir>` — Get next available report filename
- `latest-data-file <history_dir>` — Find most recent saved data JSON

All subcommands accept/return JSON. Use Bash to call them.
Pass `-` as the argument to read JSON from stdin (avoids ARG_MAX limits with large payloads):
```bash
echo '<large_json>' | python3 "${SCRIPTS_DIR}/ticket_tracker_helper.py" detect-streams -
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
/two-node:tnf-tracker                          # All tickets labeled two-node-fencing
/two-node:tnf-tracker OCPBUGS-76538            # Specific ticket(s)
/two-node:tnf-tracker 2130 2134                # Specific resource-agents PR numbers
/two-node:tnf-tracker all --diff               # Compare against previous report
/two-node:tnf-tracker --output report.md       # Save to file
```

## Jira Custom Field Reference (RHEL tickets)

| Field ID | Name | Type | Values |
|----------|------|------|--------|
| `customfield_10879` | Preliminary Testing | Dropdown | null, "Not Started", "Requested", "Pass", "Fail" |
| `customfield_10578` | Fixed in Build | Text | RPM NVR string (e.g. `resource-agents-4.10.0-108.el9_8.2`) |
| `customfield_10470` | QA Contact | User | User object (prefer `displayName`) |
| `customfield_10468` | Dev Owner | User | User object |

## Instructions

### Step 1: Parse Arguments

```bash
python3 "${SCRIPTS_DIR}/ticket_tracker_helper.py" parse-args tnf $ARGUMENTS
```

Returns JSON with `mode` ("specific" or "all"), `topologies` (will be `["tnf"]`), `pr_numbers`, `output_file`, and `diff`.

Check whether the user provided ticket keys (e.g. `OCPBUGS-76538`) or PR numbers. Ticket keys contain letters; PR numbers are purely numeric.

### Step 2: Find TNF tickets

**If specific ticket keys were provided**: Use those directly.

**If specific PR numbers were provided**: These are `ClusterLabs/resource-agents` PRs. Fetch each PR page to extract ticket references (Step 3), then proceed to Step 4.

**If mode is "all"**: Search Jira for all TNF tickets:
```
mcp__mcp-atlassian__jira_search(
  jql='labels = "two-node-fencing" ORDER BY updated DESC',
  fields="summary,status,priority,assignee,issuelinks,issuetype,labels",
  limit=30
)
```

Focus on tickets updated in the last ~6 months. Extract the ticket keys (OCPBUGS-*, OCPEDGE-*).

### Step 3: Get ticket details and find linked PRs

For each ticket, if not already fetched with full fields:
```
mcp__mcp-atlassian__jira_get_issue(issue_key="OCPBUGS-XXXXX", fields="summary,status,priority,assignee,issuelinks,issuetype,labels")
```

Extract PR links from:
1. **Issue links** — look for GitHub PR URLs in external links
2. **Issue description and comments** — scan for `github.com/<org>/<repo>/pull/<number>` URLs

Record each PR with its `repo` (org/repo) extracted from the GitHub URL. Note any linked/duplicate tickets from issue links.

### Step 4: Fetch PR details from GitHub

For each linked PR:
```
WebFetch https://github.com/<org>/<repo>/pull/<PR-number>
```

Extract PR number, title, author, status (open/merged/closed), and merge date. Tag each PR with `"topology": "tnf"` and the `"repo"` from the URL.

If PRs were provided as input (numeric args), fetch from `ClusterLabs/resource-agents` and extract ticket references from the PR title/description.

### Step 5: Group PRs by ticket

```bash
echo '<prs_json>' | python3 "${SCRIPTS_DIR}/ticket_tracker_helper.py" group-prs -
```

Input: `[{"number": 2130, "ticket_refs": ["OCPBUGS-76538"], "topology": "tnf", "repo": "ClusterLabs/resource-agents"}, ...]`
Output: groups of PRs sharing ticket references. Use this to avoid duplicate lookups.

### Step 6: Find RHEL tickets (resource-agents PRs only)

For PRs in `ClusterLabs/resource-agents`, search for associated RHEL z-stream tickets:
```
mcp__mcp-atlassian__jira_search(
  jql='project = RHEL AND text ~ "OCPBUGS-XXXXX" ORDER BY created DESC',
  fields="summary,status,priority,issuetype,fixVersions,customfield_10879,customfield_10578,customfield_10470",
  limit=15
)
```

If the search didn't return all fields, fetch individually:
```
mcp__mcp-atlassian__jira_get_issue(
  issue_key="RHEL-XXXXXX",
  fields="summary,status,fixVersions,customfield_10879,customfield_10578,customfield_10470",
  comment_limit=0
)
```

**Skip RHEL ticket search for PRs in other repos** — only `resource-agents` PRs flow through the RHEL RPM pipeline.

### Step 7: Detect streams on RHEL tickets

```bash
echo '<rhel_tickets_json>' | python3 "${SCRIPTS_DIR}/ticket_tracker_helper.py" detect-streams -
```

This adds the `stream` field to each ticket based on `fixVersions` (authoritative) or summary suffix (fallback). Important: `rhel-9.8` and `rhel-9.8.z` are different streams.

### Step 8: Validate ticket state

For each PR, run state validation:
```bash
python3 "${SCRIPTS_DIR}/ticket_tracker_helper.py" validate-state '{"pr_status": "Merged", "merge_date": "2024-03-15", "ticket_status": "ASSIGNED", "ticket_project": "OCPBUGS"}'
```

Returns `null` if valid, or a dict with `severity`, `label`, `message`, `expected_states`, and `days_since_merge`. Attach the result as `state_validation` on the PR data.

### Step 9: Check CI job status

```
WebFetch https://prow.ci.openshift.org/?repo=openshift%2Fcluster-etcd-operator&job=*two-node-fencing*
```

Key jobs: `e2e-metal-ovn-two-node-fencing-techpreview`, `e2e-metal-ovn-two-node-fencing-recovery-techpreview`, `e2e-metal-ovn-two-node-fencing-extended-techpreview`, `e2e-metal-ovn-two-node-fencing-upgrade`

Note: Resource-agents fixes land in CI via RHEL RPM rebuilds, not directly. There may be a lag between PR merge and the fix appearing in CI nightlies.

Also check PR descriptions and ticket comments for Prow job links.

### Step 10: Compare with previous report (if --diff)

```bash
python3 "${SCRIPTS_DIR}/ticket_tracker_helper.py" latest-data-file "$HISTORY_DIR"
```

If a previous data file exists, you'll use it in Step 11 after assembling the current data.

### Step 11: Assemble data and generate the report

Build the report data JSON with this structure:
```json
{
  "generated_at": "YYYY-MM-DD HH:MM UTC",
  "topologies": ["tnf"],
  "prs": [
    {
      "number": 2130,
      "topology": "tnf",
      "repo": "ClusterLabs/resource-agents",
      "title": "...",
      "author": "...",
      "status": "Merged",
      "date": "2024-03-15",
      "ticket": {
        "key": "OCPBUGS-76538",
        "project": "OCPBUGS",
        "summary": "...",
        "status": "ON_QA",
        "priority": "High",
        "assignee": "...",
        "qa_contact": "...",
        "linked_tickets": [
          {"key": "...", "summary": "...", "status": "..."}
        ]
      },
      "rhel_tickets": [
        {
          "key": "RHEL-123456",
          "summary": "...",
          "stream": "rhel-9.6.z",
          "status": "In Progress",
          "preliminary_testing": "Pass",
          "fixed_in_build": "resource-agents-4.10.0-108.el9_8.2",
          "qa_contact": "..."
        }
      ],
      "ci_status": [
        {"job": "...", "result": "Pass", "date": "...", "link": "..."}
      ],
      "state_validation": null
    }
  ],
  "ci_jobs": {
    "tnf": [
      {"job": "...", "result": "Pass", "date": "...", "link": "..."}
    ]
  },
  "diff": null
}
```

**Key schema notes:**
- Each PR has a `repo` field — PRs may come from different repos
- `rhel_tickets` is populated only for `ClusterLabs/resource-agents` PRs; empty `[]` for PRs in other repos

If `--diff` was requested and a previous data file exists, compute the diff:
```bash
python3 "${SCRIPTS_DIR}/ticket_tracker_helper.py" diff-data "$PREVIOUS_DATA_FILE" /dev/stdin <<< '<current_data_json>'
```
Set the `diff` field in the report data to the result.

Then generate the report:
```bash
echo '<report_data_json>' | python3 "${SCRIPTS_DIR}/ticket_tracker_helper.py" format-report -
```

### Step 12: Save the report

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

- Fetch all ticket details in parallel
- Fetch all PR pages from GitHub in parallel
- Fetch all RHEL ticket searches in parallel
- Fetch individual RHEL ticket details in parallel

## Notes

- Only `ClusterLabs/resource-agents` PRs go through the RHEL RPM z-stream pipeline — skip RHEL steps for PRs in other repos
- When run with `all`, focus on tickets updated in the last ~6 months to keep the report relevant
- If a PR shares a ticket with another PR, group them together
- Always save to history even if `--output` is not specified, so future `--diff` runs have a baseline
- Ignore Closed RHEL tickets when checking for z-stream gaps or duplicates
- Tickets without any linked PRs should still appear in the report (they may be in early stages)
