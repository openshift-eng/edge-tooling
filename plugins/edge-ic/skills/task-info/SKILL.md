---
name: edge-ic:task-info
description: Show detailed info about a specific TODO task — its tracked links, notes, and live Jira/PR status. Use when the user asks about a task, wants context to resume work on it, or asks what's needed to finish a ticket. Not for listing all tasks (read the TODO file directly or use edge-ic:sprint-status for sprint-wide views)
allowed-tools:
  - Read
  - Write
  - Bash
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_get_issue
user-invocable: true
argument-hint: "OCPEDGE-XXXXX or task description"
---

# IC: Task Info

Show detailed information about one task from today's TODO file, including its tracked links and their live status.

## Task

Given a task identifier, find the matching item in today's TODO file and present its full context: description, section, tracked links (PR, Doc, Jira, Build, Slack), notes, and live status pulled from Jira and GitHub.

## Steps

1. Run `date +%Y-%m-%d` to get today's date, then read `$HOME/.daily/YYYY/MM/YYYY-MM-DD.md`. If the file doesn't exist, stop and tell the user.
2. Match `$ARGUMENTS` to a task item:
   - Try an exact Jira key match first.
   - Otherwise, try a substring match against item descriptions.
   - Otherwise, try a positional match (e.g., "item 2").
3. If zero matches, tell the user and list the closest candidates instead of guessing. If multiple matches, show them with their section and ask the user to pick one.
4. Display the matched item's description, section, and checkbox state, plus every indented sub-line beneath it verbatim.
5. For each `Jira:` sub-line, extract the ticket key from the URL and call `mcp__plugin_mcp-atlassian_mcp-atlassian__jira_get_issue` for status, assignee, priority, and the most recent comment. If the call fails, note "Could not reach Jira" and continue with the static info.
6. For each `PR:` sub-line, run `gh pr view <url> --json state,title,reviewDecision,statusCheckRollup,url`. If `gh` isn't available or the call fails, note "Could not fetch PR status" and continue.
7. Display `Doc:`, `Build:`, `Slack:`, and `Note:` sub-lines as-is — no live lookup needed.
8. If the task's description contains a bare Jira key (e.g., `OCPEDGE-2510`) but has no `Jira:` sub-line, ask the user if they want one added. If yes, append `- Jira: https://redhat.atlassian.net/browse/<KEY>` (indented two spaces beneath the item) and write the file.
9. Present the summary (see Output Structure).

## Output Structure

```text
## <task description>
Section: Priority | Status: not started

Jira: OCPEDGE-2510 — In Progress · High · assigned to jroche
  https://redhat.atlassian.net/browse/OCPEDGE-2510
  Latest comment: "..."

PR: #1234 open (approved)
  https://github.com/openshift/installer/pull/1234

Doc: https://docs.openshift.com/container-platform/4.18/installing/

Note: Reproduces on 4.18 nightly only, needs metal cluster
```

Omit any link-type line the task doesn't have. If a live lookup failed, show the static link with the failure note instead of the live fields.

## Edge Cases

- **No task matches**: list the closest candidates by substring, don't guess and don't modify the file.
- **Task has no sub-lines**: show the description and section, then note "No links tracked for this task yet — add one with `/edge-ic:daily-link`."
- **Jira or GitHub lookup fails**: degrade gracefully — show the static link and a short failure note, don't stop the whole command.
- **Task exists in a past day's file, not today's**: this skill only searches today's file. Tell the user to check that day's file directly, or that `weekly-wrapup` carries forward incomplete items week to week.

## Notes

- Read-only for the TODO file, except the single opt-in write in step 8 (adding a missing Jira link), which always requires user confirmation first.
- Only searches today's TODO file — no cross-day search.
