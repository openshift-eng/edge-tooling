---
name: manage-ticket
description: >-
  Create, update, transition, link, and comment on Jira tickets with Edge Scrum
  Law enforcement. Use when the user says "create a ticket", "update PROJ-XXX",
  "move PROJ-XXX to In Progress", "transition PROJ-XXX", "link these tickets",
  "add a comment to PROJ-XXX", or any request to modify Jira issues.
user-invocable: true
argument-hint: "<action> [TICKET-KEY] [fields...]"
allowed-tools:
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_create_issue
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_update_issue
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_transition_issue
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_add_comment
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_create_issue_link
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_get_issue
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_get_transitions
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_search
  - Read
  - AskUserQuestion
---

## Step 0: Load Laws

Read the following Edge Scrum Law files from `${CLAUDE_PLUGIN_ROOT}/../edge-scrum/references/laws/`:

- `01-jira-projects.md` (project keys, labels, components)
- `02-jira-stories.md` (story/spike/task conventions)
- `03-jira-bugs.md` (bug conventions)
- `04-jira-epics.md` (epic conventions)
- `06-jira-fields.md` (custom field IDs)
- `07-workflow-states.md` (valid states and transitions)
- `14-agent-conventions.md` (agent-specific rules)

If edge-scrum plugin is not installed, skip law loading and apply only basic validation.

## Step 1: Parse Intent

Determine the user's intent from their request:

- **Create**: "create a ticket", "new story", "new bug", etc.
- **Update**: "update PROJ-XXX", "set story points on PROJ-XXX", "change assignee", etc.
- **Transition**: "move PROJ-XXX to In Progress", "close PROJ-XXX", "transition", etc.
- **Link**: "link PROJ-XXX to PROJ-YYY", "block PROJ-XXX", etc.
- **Comment**: "add a comment to PROJ-XXX", "comment on PROJ-XXX", etc.

If the intent is ambiguous, ask the user to clarify.

## Step 2: Execute with Law Enforcement

### Create

1. Ask for or infer: project (OCPEDGE, USHIFT, OCPBUGS), issue type (Story, Bug, Task, Spike, Epic), summary
2. Enforce required fields per issue type:
   - **Story/Spike/Task**: Epic Link, Story Points (Fibonacci: 0,1,2,3,5,8,13). Prompt for missing fields.
   - **Bug**: Story Points MUST be 0. Set automatically, don't ask.
   - **Epic**: Component (must be valid workstream), T-shirt Size (XS/S/M/L/XL), QA Contact, Doc Contact (set to `unassigned_jira` if user says not needed). Prompt for missing fields.
3. Validate all field values against Laws before creating.
4. Confirm the full ticket details with the user before calling `jira_create_issue`.
5. After creation, report the new ticket key and URL.

### Update

1. Fetch the current ticket state with `jira_get_issue`.
2. Validate the proposed changes against Laws:
   - SP changes must be Fibonacci. Bugs must stay 0.
   - Component changes must use valid workstream names.
   - Version fields must use `X.Y.0` format.
3. Confirm changes with the user before calling `jira_update_issue`.

### Transition

1. Fetch the current ticket state and available transitions with `jira_get_transitions`.
2. Validate the transition is legal per `07-workflow-states.md`:
   - Epic → In Progress: Target Version MUST be set. If missing, ask the user to set it first.
   - Epic → Dev Complete: Fix Version MUST be set. If missing, ask.
3. Confirm the transition with the user before calling `jira_transition_issue`.

### Link

1. Validate both issue keys exist with `jira_get_issue`.
2. Determine link type (blocks, is blocked by, relates to, etc.).
3. Create the link with `jira_create_issue_link`.

### Comment

1. Validate the issue key exists.
2. Ask for or use the provided comment text.
3. Add the comment with `jira_add_comment`.

## Important Rules (from Laws)

- NEVER suggest non-zero SP for bugs.
- NEVER manually change the Epic Status field (automation handles it).
- NEVER leave QA Contact or Doc Contact blank on Epics — use `unassigned_jira` if no owner.
- LVMS bugs: Release Blocker MUST be "Rejected".
- Version format is always `X.Y.0` (e.g., `4.18.0`), never `4.18` alone.
- Valid components: MicroShift, Two Node with Arbiter, Two Node with Fencing, SNO, Logical Volume Manager Storage, Bandwidth Reduction, Topology Transition.
- Always confirm significant changes with the user before executing.
