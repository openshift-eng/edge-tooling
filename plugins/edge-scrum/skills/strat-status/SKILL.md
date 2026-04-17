---
name: edge-scrum:strat-status
description: Query a STRAT ticket and display its epic hierarchy with rollup statistics
allowed-tools: mcp__atlassian__jira_get_issue, mcp__atlassian__jira_search, AskUserQuestion
user-invocable: true
---

# Edge Scrum: STRAT Hierarchy

Query a STRAT (strategic initiative) ticket and display its epic hierarchy with rollup statistics, ticket counts, and organization suggestions.

## Task

Given a STRAT ticket key, query all epic children and their tickets to provide a view of the strategic initiative's progress and suggest organizational improvements.

## Instructions

1. **Check for required STRAT key**:
   - If no arguments provided or first argument is a flag (starts with `--`), use `AskUserQuestion` to prompt: "Which STRAT would you like to check? (Provide the STRAT key, e.g., OCPSTRAT-1551)"
   - Wait for user response before proceeding

2. **Parse arguments**:
   - STRAT key (required): First positional argument (e.g., `OCPSTRAT-100`)
   - `--include-done`: Optional flag to show completed epics (default: hide done)
   - `--assignee=<user>`: Optional filter for epics with tickets assigned to user
   - `--format=<type>`: Optional format (`table`, `simple`, `keys-only`; default: `table`)

3. **Fetch STRAT details**:
   - Use `mcp__atlassian__jira_get_issue` with STRAT key
   - Fields: `key,summary,status,description`
   - Extract STRAT summary for display

4. **Query epic children**:
   - Build JQL based on flags:
     - Base: `parent = <strat-key> AND issuetype = Epic`
     - If NOT `--include-done`: Add `AND statusCategory != Done`
     - Order: `ORDER BY status ASC, priority DESC`
   - Use `mcp__atlassian__jira_search`
   - Fields: `key,summary,status,priority`
   - **Pagination**: Use `maxResults=100` and iterate with `startAt` to fetch all epics
     - Request pages until fewer results returned or no more results
     - Aggregate all pages to ensure rollup counts are accurate

5. **For each epic, query child tickets**:
   - Build JQL with all filters before ORDER BY:
     - Base: `parent = <epic-key>`
     - If `--assignee` provided: Validate and add `AND assignee = <user>`
       - **Validation**: Prefer Jira accountId (UUID format) when available
       - If using email/username, escape special JQL characters and wrap in quotes
       - Reject inputs with disallowed characters (semicolons, quotes that break JQL)
       - Example safe formats: `557058:f58131cb-b67d-43c7-b30d-6b58d40bd077` (accountId) or `"user@example.com"` (email)
     - Order: `ORDER BY status ASC, priority DESC`
   - Fields: `key,summary,status,assignee,priority`
   - **Pagination**: Use `maxResults=100` and iterate with `startAt` to fetch all tickets
     - Request pages until fewer results returned or no more results
     - Aggregate all pages before counting by status category
   - Count tickets by status category across all pages

6. **Calculate epic statistics and rollups**:
   - For each epic: Count tickets by status category (To Do, In Progress, Done)
   - For STRAT: Sum all epic ticket counts
   - Calculate completion percentage:
     - **Guard against division by zero**: If Total is 0, set completion percentage to 0%
     - Otherwise: `(Done / Total) * 100`
   - Track epic count by status category
   
7. **Generate suggestions**:
   - If epic has all tickets done but epic status != Done: "Epic <key> ready to close"
   - If STRAT has epics with no active tickets: "Consider moving to Release Pending"
   - If STRAT completion > 80%: "Near completion - review for closure"
   
8. **Format output based on --format**:
   
   **table format (default)**:

   ```markdown
   ## STRAT: <key> - <summary>
   
   ### Epics (<count> active)
   
   | Epic Key | Summary | To Do | In Progress | Done | Total |
   |----------|---------|-------|-------------|------|-------|
   | ...      | ...     | ...   | ...         | ...  | ...   |
   
   ### Rollup Statistics
   - Total Epics: <count> (<active> active, <done> done)
   - Total Tickets: <count> (<to-do> To Do, <in-progress> In Progress, <done> Done)
   - Completion: <percentage>%
   
   ### Suggestions
   - <suggestion text>
   ```

   **simple format**:

   ```markdown
   STRAT: <key> - <summary>
   
   Epics (<count>):
   - <EPIC-KEY>: <summary> (<to-do> To Do, <in-progress> In Progress, <done> Done)
   
   Total: <count> epics, <count> tickets (<percentage>% complete)
   ```

   **keys-only format**:

   ```markdown
   <EPIC-KEY>
   <EPIC-KEY>
   <EPIC-KEY>
   ```

9. **Handle edge cases**:
   - If no epics found: Display "No [active] epics found for STRAT <key>"
   - If STRAT not found: Display error message from Jira API
   - Empty status categories: Show 0 in counts
   - If Total tickets is 0: Set completion percentage to 0% (guard against division by zero)
   - If assignee filter yields no results: Display "No epics with tickets assigned to <user>"

## Important

- **Default filters out done** - Only show active epics unless --include-done
- **Hierarchical structure** - STRAT → Epics → Ticket counts
- **Rollup statistics** - Provide both epic-level and STRAT-level stats
- **Suggestions** - Help user identify organizational actions
- **Status categories** - Use statusCategory for grouping (To Do, In Progress, Done)
- Use "active epics" in output when --include-done is NOT used
- Use "epics" in output when --include-done IS used

## Example Execution

```bash
Input: /edge-scrum:strat-status OCPSTRAT-1551

JQL (epics): parent = OCPSTRAT-1551 AND statusCategory != Done ORDER BY status ASC, priority DESC
JQL (tickets): parent = <each-epic-key> ORDER BY status ASC, priority DESC

Output:
## STRAT: OCPSTRAT-1551 - Edge Platform Improvements

### Epics (3 active)

| Epic Key | Summary | To Do | In Progress | Done | Total |
|----------|---------|-------|-------------|------|-------|
| OCPEDGE-2440 | Two-Node Plugin Refactoring | 2 | 1 | 5 | 8 |
| OCPEDGE-2510 | Pacemaker Health Checks | 3 | 2 | 0 | 5 |
| OCPEDGE-2561 | Payload Monitor Intelligence | 1 | 0 | 3 | 4 |

### Rollup Statistics
- Total Epics: 3 (3 active, 0 done)
- Total Tickets: 17 (6 To Do, 3 In Progress, 8 Done)
- Completion: 47%

### Suggestions
- Epic OCPEDGE-2440 near completion - review remaining work
```

## Notes

- **Sprint planning** - Use to identify which epics to prioritize
- **STRAT organization** - Suggestions help move epics between STRATs
- **Progress tracking** - Rollup statistics show overall STRAT health

## Edge Scrum Laws Reference

This skill uses conventions from:
- [`references/laws/05-jira-features.md`](../../references/laws/05-jira-features.md) - Feature and Initiative (STRAT) conventions
- [`references/laws/04-jira-epics.md`](../../references/laws/04-jira-epics.md) - Epic conventions and lifecycle
- [`references/laws/07-workflow-states.md`](../../references/laws/07-workflow-states.md) - Valid workflow states and status categories
