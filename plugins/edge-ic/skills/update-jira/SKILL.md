---
name: edge-ic:update-jira
description: Update Jira issues based on today's TODO accomplishments
allowed-tools:
  - Read
  - mcp__atlassian__jira_add_comment
  - mcp__atlassian__jira_transition_issue
  - mcp__atlassian__jira_add_worklog
  - AskUserQuestion
user-invocable: true
---

# IC: Update Jira

Update Jira issues based on today's TODO accomplishments.

## Task

Read today's TODO file and update related Jira issues with accomplishments, status changes, and comments based on completed and in-progress work.

## Instructions

1. **Read today's TODO file**: `$HOME/.daily/YYYY/MM/YYYY-MM-DD.md`
2. **Identify Jira issues**: Extract ticket keys and accomplishment text
3. **Prompt for updates** for each ticket
4. **Execute updates using MCP tools**:
   - `mcp__atlassian__jira_add_comment`
   - `mcp__atlassian__jira_transition_issue`
   - `mcp__atlassian__jira_add_worklog`
5. **Display summary** with clickable Jira links

## Important

- Only process tickets from today's TODO
- Prompt before making any changes
- Preserve accomplishment text when adding comments
- Validate status transitions
- Use batch operations for efficiency
