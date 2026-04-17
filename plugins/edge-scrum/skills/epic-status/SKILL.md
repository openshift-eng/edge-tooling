---
name: edge-scrum:epic-status
description: Query an epic and display its child tickets with status breakdown
allowed-tools: mcp__atlassian__jira_get_issue, mcp__atlassian__jira_search, AskUserQuestion
user-invocable: true
---

# Edge Scrum: Epic Status

Query all child tickets of a given epic and display them grouped by status to show epic progress and remaining work.

## Task

Given an epic key (and optional filters), query Jira for all child tickets and present them in a structured format grouped by status category.

## Instructions

1. **Check for required epic key**:
   - If no arguments provided or first argument is a flag (starts with `--`), use `AskUserQuestion` to prompt: "Which epic would you like to check? (Provide the epic key, e.g., OCPEDGE-2431)"
   - Wait for user response before proceeding

2. **Parse arguments**:
   - Epic key (required): First positional argument (e.g., `PROJECT-200`)
   - `--include-done`: Optional flag to include completed tickets (default: filter out statusCategory=Done)
   - `--assignee=<user>`: Optional filter for specific assignee (email, `currentUser()`, or `Unassigned`)
   - `--format=<type>`: Optional format (`table`, `simple`, `keys-only`; default: `table`)

3. **Fetch epic information**:
   - Use `mcp__atlassian__jira_get_issue` to get epic summary
   - Epic key: provided argument
   - Fields: `summary`

4. **Build JQL query**:
   - Base: `parent = <epic-key>`
   - Default behavior: Add `AND statusCategory \!= Done` (unless `--include-done` specified)
   - If `--assignee` provided: Add `AND assignee = <user>`
   - Order by: `status ASC, priority DESC`

5. **Query child tickets with pagination**:
   - Use `mcp__atlassian__jira_search` with the JQL query
   - Fields: `key,summary,status,assignee,priority`
   - Pagination: Use `limit=50` and `page_token` to fetch all results
     - Initial request: `limit=50`, no `page_token`
     - Check response for `has_more` field
     - If `has_more` is true, pass `page_token` from previous response to next call
     - Continue until `has_more` is false
     - Aggregate all pages into final result list

6. **Group tickets by status category**:
   - **In Progress**: statusCategory = "In Progress"
   - **To Do**: statusCategory = "To Do"
   - **Done**: statusCategory = "Done" (only if `--include-done`)
   - Sort within groups by status name for progression visibility

7. **Format output based on --format**:
   
   **table format (default)**:

   ```markdown
   ## Epic: <key> - <summary>
   
   ### <Category> (<count> tickets)
   
   | Key | Status | Assignee | Priority | Summary |
   |-----|--------|----------|----------|---------|
   | ... | ...    | ...      | ...      | ...     |
   
   **Total: <count> [active] tickets** (<In Progress count>, <To Do count>[, <Done count>])
   ```

   **simple format**:

   ```markdown
   Epic: <key> - <summary>
   
   <Category> (<count>):
   - <KEY>: <Summary>
   
   Total: <count> tickets
   ```

   **keys-only format**:

   ```markdown
   <KEY>
   <KEY>
   <KEY>
   ```

8. **Handle edge cases**:
   - If no tickets found: Display "No [active] tickets found for epic <key>"
   - If epic not found: Display error message
   - Empty status categories: Don't display the section

## Important

- **Default filters out completed work**: Unless `--include-done` is specified, only show active tickets (statusCategory \!= Done)
- This focuses attention on remaining work rather than epic history
- Use "active tickets" in summary when --include-done is NOT used
- Use "tickets" in summary when --include-done IS used
- Assignee display name should be used for readability (not email)
- Unassigned tickets show "Unassigned" in assignee column

## Edge Scrum Laws Reference

This skill uses conventions from:
- [`references/laws/04-jira-epics.md`](../../references/laws/04-jira-epics.md) - Epic conventions and lifecycle
- [`references/laws/07-workflow-states.md`](../../references/laws/07-workflow-states.md) - Valid workflow states and status categories

## Example Execution

```bash
Input: /edge-scrum:epic-status PROJECT-200

JQL: parent = PROJECT-200 AND statusCategory \!= Done ORDER BY status ASC, priority DESC

Output:
## Epic: PROJECT-200 - Non-Blocking CI Updates

### To Do (4 tickets)

| Key | Status | Assignee | Priority | Summary |
|-----|--------|----------|----------|---------|
| PROJECT-201 | To Do | User A | Critical | Node replacement test fails |
| PROJECT-202 | To Do | Unassigned | Critical | Add regression component |
| PROJECT-203 | To Do | User B | Normal | Update CI documentation |
| PROJECT-204 | To Do | User A | Minor | Clean up test artifacts |

**Total: 4 active tickets** (0 In Progress, 4 To Do)
```

## Notes

- Useful for sprint planning to see what epic work remains
- Combine with assignee filter to see your work in an epic
- Use --include-done for retrospectives or completion summaries
