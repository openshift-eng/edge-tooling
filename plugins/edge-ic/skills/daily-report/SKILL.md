---
name: edge-ic:daily-report
description: Generate daily standup report from TODO file in Slack-ready format
allowed-tools:
  - Read
  - Write
  - Bash
user-invocable: true
---

# IC: Daily Report

Generate a daily standup report from today's TODO file in Slack-ready format.

## Task

Read today's TODO file (`$HOME/.daily/YYYY/MM/YYYY-MM-DD.md`) and generate a daily report following the format specified in `plugins/edge-ic/references/DAILY_REPORT_FORMAT.md`.

## Instructions

1. **Read the format specification** from `plugins/edge-ic/references/DAILY_REPORT_FORMAT.md`
2. **Read today's TODO file** from `$HOME/.daily/YYYY/MM/` using today's date
3. **Parse all sections systematically** (read every item from TODO file):
   - **Completed section**: Parse EVERY completed item
   - **In Sprint (Blocked/Waiting)**: Parse ALL blocked items
   - **In Sprint (Available to Work)**: Parse ALL in-progress items
4. **Consolidate into report bullets** (group related items together):
   - Group related completed items together where appropriate
   - Combine multiple Jira tickets for the same work into one bullet (e.g., `TICKET-123, TICKET-456: Description`)
   - Keep bullets high-level and avoid excessive detail
5. **Generate the report** with:
   - Header line at the top for `/copy` command alignment
   - `:done-circle-check:` for completed items
   - `:in-progress:` for in-progress items
   - `:jira-blocker:` for blocked items
   - Concise descriptions
   - Jira tickets: `TICKET-ID: Description (https://redhat.atlassian.net/browse/TICKET-ID)`
   - Multiple tickets: `TICKET-123, TICKET-456: Description (https://redhat.atlassian.net/browse/TICKET-123, https://redhat.atlassian.net/browse/TICKET-456)`
6. **Render as plain text** ready for Slack
7. **Validate format:**
   - Write report to temporary file (e.g., `/tmp/daily-report-YYYY-MM-DD.txt`)
   - Run validation: `plugins/edge-ic/bin/validate-daily-report.sh /tmp/daily-report-YYYY-MM-DD.txt`
   - Fix any errors reported by the validator
   - Address warnings as best effort
8. **Check with user** before finalizing:
   - Ask if there are any additional updates or items to include
   - Ask if any bullets need revision or consolidation
   - Confirm report is ready to post
9. **After report is accepted**, prompt user:
   - "Would you like me to update any Jira tickets based on today's progress?"
   - Offer to update status, add comments, or transition tickets as needed

## Important

**CRITICAL Guardrails**:

1. **Completeness**: Include ALL completed items, in-progress items, and blocked items from the TODO file
2. **Consolidation**: Group similar items into single high-level bullets - avoid excessive detail
3. **Validation**: Run `plugins/edge-ic/bin/validate-daily-report.sh` and fix any errors, address warnings as best effort
4. **User Confirmation**: Always check with the user for additional updates before finalizing the report

This is a systematic translation task with intelligent consolidation, not a summarization task that omits items.

## Example Output Format

```text
Daily Report:

:done-circle-check: Updated weekly team report with risks across components
:done-circle-check: PROJECT-100: Both PRs merged - issue resolved (https://redhat.atlassian.net/browse/PROJECT-100)
:in-progress: PROJECT-102: IPv4 testing pending for network fix
```
