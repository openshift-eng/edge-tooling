# jira-helpers

Developer-focused Jira workflow integration for the OpenShift Edge Team -- ticket validation, management, convention enforcement, and sprint planning.

## Prerequisites

| Requirement | Details |
|-------------|---------|
| `JIRA_USERNAME` | Environment variable set to your Red Hat Jira email |
| `JIRA_API_TOKEN` | Environment variable with a Jira API token ([create one](https://id.atlassian.com/manage-profile/security/api-tokens)) |
| `mcp-atlassian` | Container image pulled automatically via podman at runtime |

## Installation

```
/plugin marketplace add openshift-eng/edge-tooling jira-helpers
```

## Skills

| Skill | Description | Example |
|-------|-------------|---------|
| `validate-ticket` | Validate a Jira ticket against Edge team conventions (required fields, sizing, description template) | `/validate-ticket OCPEDGE-1234` |
| `my-tickets` | List your assigned tickets filtered by status, sprint, or project | `/my-tickets` |
| `manage-ticket` | Create, update, or transition a Jira ticket with convention guardrails | `/manage-ticket transition OCPEDGE-1234 to "In Progress"` |
| `plan-sprint` | Assist with sprint planning -- capacity analysis, ticket grooming, and commitment recommendations | `/plan-sprint` |

## Hooks

| Hook | Event | Description |
|------|-------|-------------|
| `ticket-mention` | `UserPromptSubmit` | Detects Jira ticket keys in user prompts and prefetches ticket context |
| `pr-title-check` | `PreToolUse` | Validates PR titles include a Jira ticket reference before creation |
| `plan-to-jira` | `PostToolUse` | Offers to create/update Jira tickets when a plan is approved via ExitPlanMode |

## Dependencies

The **edge-scrum** plugin is a soft dependency. Validation rules and sprint conventions are enriched when edge-scrum is installed, but all skills degrade gracefully without it.
