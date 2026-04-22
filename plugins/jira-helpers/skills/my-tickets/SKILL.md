---
name: my-tickets
description: >-
  View and filter Jira tickets assigned to the current user. Use when the user
  says "show my tickets", "what's on my plate", "what am I working on",
  "my assigned issues", "my backlog", or asks about their current Jira workload.
user-invocable: true
argument-hint: "[--sprint active|future] [--project PROJ] [--status STATUS] [--epic EPIC-KEY]"
allowed-tools:
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_search
  - Read
  - Bash
  - Write
---

## Step 0: Resolve User Identity
Read the `JIRA_USERNAME` environment variable using Bash: `echo "$JIRA_USERNAME"`. This is the user's Jira email address used for assignee queries.

## Step 1: Build JQL Query
Start with base JQL:
```
assignee = "{JIRA_USERNAME}" AND status not in (Closed, Verified, Done) ORDER BY priority ASC
```

Apply filters from arguments:
- `--sprint active`: add `AND sprint in openSprints()`
- `--sprint future`: add `AND sprint in futureSprints()`
- `--project PROJ`: add `AND project = "PROJ"`
- `--status "STATUS"`: replace the status exclusion with `AND status = "STATUS"`
- `--epic EPIC-KEY`: add `AND "Epic Link" = "EPIC-KEY"`

If no arguments provided, use the base query (all open assigned tickets).

## Step 2: Fetch Issues
Call `jira_search` with the built JQL. Request fields: `key, summary, status, issuetype, assignee, updated, labels, issuelinks, customfield_10028, customfield_10014, customfield_10021`.

Handle pagination: if the response indicates more results, fetch subsequent pages using `page_token`.

Save the raw response to a temp file.

## Step 3: Transform
Run `python3 ${CLAUDE_PLUGIN_ROOT}/bin/transform-my-issues.py <temp_file> -o <output_file>` to structure the data.

Read the output JSON.

## Step 4: Display
Present results in a markdown table:

```
| Key | Summary | Status | Type | SP | Epic | Sprint | Updated | Flags |
```

Where Flags shows:
- 🚩 if flagged (impediment)
- ⛔ if blocked by another issue
- ⏰ if stale (>5 days since update and In Progress/Review)

Below the table, show aggregates:
- Total issues: X (Y SP)
- By status: To Do: X, In Progress: Y, Review: Z
- By project: OCPEDGE: X, USHIFT: Y, OCPBUGS: Z
