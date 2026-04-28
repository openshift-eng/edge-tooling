---
name: jira-helpers:plan-sprint
description: >-
  Analyze backlog and suggest a sprint plan targeting 8 SP capacity. Use when
  the user says "plan my sprint", "what should I pull into next sprint",
  "sprint prep", "sprint planning", "prepare for sprint", or asks about
  planning their upcoming sprint work.
user-invocable: true
argument-hint: "[sprint-number]"
allowed-tools:
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_search
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_get_agile_boards
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_get_sprints_from_board
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_get_issue
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_update_issue
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_add_issues_to_sprint
  - Read
  - Bash
  - Write
  - AskUserQuestion
  - Agent
---

# Plan Sprint

## Step 0: Load Context
1. Read the `JIRA_USERNAME` environment variable using Bash: `echo "$JIRA_USERNAME"`.
2. Read Edge Scrum Laws from `${CLAUDE_PLUGIN_ROOT}/../edge-scrum/references/laws/`:
   - `06-jira-fields.md` (custom field IDs)
   - `09-sprint-policies.md` (capacity rules: 8 SP target per person per sprint)
   - `02-jira-stories.md` (pointing conventions)
   If edge-scrum is not installed, use defaults: 8 SP target, Fibonacci pointing.

## Step 1: Identify Target Sprint
1. Resolve the board ID: call `jira_get_agile_boards` with `project_key="OCPEDGE"`. If multiple boards are returned, ask the user to select one. Use the resolved board ID for all subsequent sprint calls.
2. Call `jira_get_sprints_from_board` with the resolved board ID and `state="active"` to get the active sprint.
3. Call `jira_get_sprints_from_board` with the resolved board ID and `state="future"` to get future sprints.
4. If the user provided a sprint number, match it. Otherwise, use the next future sprint. If no future sprint exists, ask the user which sprint to plan for.
5. Note the target sprint ID and name.

## Step 2: Assess Current Sprint Load
1. Query active sprint tickets assigned to the user:
   ```
   assignee = "{JIRA_USERNAME}" AND sprint in openSprints() AND project in (OCPEDGE, USHIFT, OCPBUGS) ORDER BY priority ASC
   ```
2. Request fields: `key, summary, status, issuetype, priority, description, customfield_10028, customfield_10014, customfield_10021, issuelinks`.
3. Calculate:
   - Total committed SP in active sprint
   - SP completed (status in Done, Closed, Verified)
   - SP remaining (not completed)
   - Likely carryover: items still "To Do" or "In Progress" at this point in the sprint

## Step 3: Fetch Backlog
1. Query the user's backlog (assigned but not in a sprint and not closed):
   ```
   assignee = "{JIRA_USERNAME}" AND sprint is EMPTY AND status not in (Closed, Verified, Done) AND project in (OCPEDGE, USHIFT, OCPBUGS) ORDER BY priority ASC
   ```
2. Request same fields as Step 2.
3. Transform results using `python3 ${CLAUDE_PLUGIN_ROOT}/bin/transform-my-issues.py`.

## Step 4: Analyze and Suggest
1. Calculate available capacity: 8 SP target minus expected carryover SP.
2. From the backlog, suggest tickets to fill the capacity:
   - Prioritize by: priority field, epic progress (issues in epics with existing sprint work), dependencies (unblocked first)
   - Stop when cumulative SP reaches or exceeds the target
3. Flag ticket quality gaps in the suggested set:
   - [BLOCKER] Unpointed — no story points assigned, cannot plan without points
   - [BLOCKER] Missing Epic Link — Story/Spike/Task without `customfield_10014`
   - [WARNING] Missing acceptance criteria in description
   - [WARNING] Bug has non-zero story points (should be 0)

## Step 5: Present Sprint Plan
Display the proposed plan:

### Carryover from Active Sprint
| Key | Summary | SP | Status |
(items expected to carry over)

### Suggested for Next Sprint
| Key | Summary | SP | Epic | Gaps |
(items from backlog)

### Summary
- Target capacity: 8 SP
- Expected carryover: X SP
- New work suggested: Y SP
- Total planned: Z SP
- Capacity status: On target / Over/under by N SP

### Ticket Gaps Found
List any issues that need fixing before sprint planning.

## Step 6: Fix Gaps (Optional)
If there are gaps (unpointed tickets, missing epic links, etc.), ask the user:
"Would you like to fix these gaps before adding tickets to the sprint?"

If yes:
- For unpointed: ask the user for point estimates
- For missing epic link: ask which epic to link to
- For bug SP: auto-fix to 0
- Apply fixes via `jira_update_issue`

## Step 7: Apply Plan (Optional)
After presenting the plan and fixing gaps, ask:
"Would you like to move these tickets into {sprint name}?"

If yes: call `jira_add_issues_to_sprint` with the sprint ID and the list of issue keys.

Report the final state after assignment.
