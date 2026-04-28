---
name: validate-ticket
description: >-
  Validate a Jira ticket against Edge Scrum Laws. Use when the user says
  "validate ticket", "check ticket", "is this ticket well-formed", mentions
  validating a Jira issue, or when the ticket-mention hook detects ticket keys.
user-invocable: true
argument-hint: "<TICKET-KEY>"
allowed-tools:
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_get_issue
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_search
  - mcp__plugin_mcp-atlassian_mcp-atlassian__jira_update_issue
  - Read
  - Bash
  - AskUserQuestion
---

## Step 0: Load Laws

Read the following Edge Scrum Law files from the edge-scrum plugin. Use paths relative to this plugin: `${CLAUDE_PLUGIN_ROOT}/../edge-scrum/references/laws/`. If the edge-scrum plugin is not installed, skip law loading and note that only basic field checks will be performed.

Law files to load based on issue type:
- Always: `06-jira-fields.md` (custom field IDs)
- Story/Spike/Task: `02-jira-stories.md`
- Bug: `03-jira-bugs.md`
- Epic: `04-jira-epics.md`, `07-workflow-states.md`

## Step 1: Fetch Ticket

Use `jira_get_issue` with the ticket key from the user's argument. Request fields: `summary, status, issuetype, assignee, components, labels, description, updated, customfield_10028, customfield_10014, customfield_10018, customfield_10021, customfield_10470, customfield_10473, customfield_10795, fixVersions, versions`.

If the ticket key is not provided, ask the user for it.

## Step 2: Run Validation Checks

Based on the issue type, run the following checks. Group findings by severity.

### CRITICAL (must fix)

- **Bug SP must be 0:** If issue type is Bug and `customfield_10028` (Story Points) is not 0 or null, flag it.
- **Story/Spike/Task must have Epic Link:** If issue type is Story, Spike, or Task and `customfield_10014` (Epic Link) is empty/null, flag it.
- **Epic QA/Doc Contact not blank:** If issue type is Epic and `customfield_10470` (QA Contact) or `customfield_10473` (Doc Contact) is null/empty, flag it. They should be set to a user or `unassigned_jira`.
- **SP must be Fibonacci:** If issue type is Story, Spike, or Task and `customfield_10028` is not in {0, 1, 2, 3, 5, 8, 13}, flag it.
- **LVMS bug Release Blocker:** If issue is a Bug with component "Logical Volume Manager Storage" and Release Blocker field is not "Rejected", flag it.

### HIGH (should fix)

- **Epic In Progress needs Target Version:** If Epic status is "In Progress" and Target Version (versions) is empty, flag it.
- **Epic Dev Complete needs Fix Version:** If Epic status is "Dev Complete" and fixVersions is empty, flag it.
- **Version format:** Any version field value must match pattern `X.Y.0` (e.g., `4.18.0`, `5.2.0`). Flag if format is wrong (e.g., `4.18` without `.0`).
- **Component required for Epics:** If Epic has no components, flag it. Valid components: MicroShift, Two Node with Arbiter, Two Node with Fencing, SNO, Logical Volume Manager Storage, Bandwidth Reduction, Topology Transition.
- **T-shirt size required:** If Epic and `customfield_10795` (T-shirt Size) is empty, flag it. Valid sizes: XS, S, M, L, XL.

### MEDIUM (nice to fix)

- **Missing acceptance criteria:** If Story or Spike and description does not contain "Acceptance Criteria", "AC:", or a checklist pattern, flag it.
- **No labels:** If labels array is empty, flag it.
- **Stale:** If status is "In Progress" or "Review" and the `updated` field is more than 5 business days ago, flag it.

## Step 3: Report

Present findings in a clear table grouped by severity. For each finding, show:
- Severity: CRITICAL, HIGH, MEDIUM
- The rule violated
- Current value (if applicable)
- Expected value

If no findings: report "Ticket passes all validation checks."

## Step 4: Offer Fixes

For fixable issues, offer to auto-correct:
- Bug SP → set to 0 via `jira_update_issue`
- QA/Doc Contact blank → set to `unassigned_jira` via `jira_update_issue`
- Version format → correct to `X.Y.0` format

Ask the user before making any changes. Apply all approved fixes in a single update if possible.
