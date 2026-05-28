---
name: edge-ic:weekly-wrapup
description: End-of-week automation - review active work and prepare next week's TODO
allowed-tools:
  - Read
  - Write
  - mcp__atlassian__jira_search
  - mcp__atlassian__jira_add_comment
  - mcp__atlassian__jira_transition_issue
  - AskUserQuestion
user-invocable: true
---

# IC: Weekly Wrap-up

Perform end-of-week workflow automation: review active Jira tickets, add status updates, and prepare TODO file for next week.

## Task

Automate the weekly wrap-up process by reviewing active work, gathering status updates, and creating a structured TODO file for the upcoming week with carry-over items and suggested priorities.

## Instructions

1. **Parse arguments**: `--dry-run`, `--no-jira`, `--no-todo`
2. **Query active Jira tickets**: assignee = currentUser() AND statusCategory IN ("In Progress", "To Do")
3. **Group tickets by status**: Code Review, POST, In Progress, ASSIGNED, To Do
4. **Prompt for status updates** for each ticket
5. **Read current week's TODO files**: Resolve Monday's date from current week and read `$HOME/.daily/YYYY/MM/YYYY-MM-DD.md`. File must conform to format defined in `plugins/edge-ic/references/TODO_FILE_FORMAT.md`. If validation fails in `--dry-run` mode, display errors but continue; otherwise abort.
6. **Generate next Monday's TODO file**: Resolve next Monday's date and write to `$HOME/.daily/YYYY/MM/YYYY-MM-DD.md`. Generated file must pass validation against `plugins/edge-ic/references/TODO_FILE_FORMAT.md` before writing. If validation fails in `--dry-run` mode, display errors but continue; otherwise abort.
7. **Generate backlog suggestions**
8. **Display summary**

## Important

- Interactive by default - User must confirm each status update
- Use currentUser() in JQL
- Preserve context in carry-over items
- Support --dry-run to preview before committing

### TODO File Format and Validation

- TODO files are stored at `$HOME/.daily/YYYY/MM/YYYY-MM-DD.md`
- Files must conform to format defined in `plugins/edge-ic/references/TODO_FILE_FORMAT.md`
- Validation is enforced for both read and write operations
- Validation failures abort the operation (except in --dry-run mode, which displays errors and continues)
- User-invocable skill - only invoke when the user explicitly requests weekly wrap-up automation
