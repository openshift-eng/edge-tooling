# two-node

Two-node topology (TNA/TNF) workflow automation for OpenShift edge deployments.

## Installation

Install via Claude Code's plugin system:

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install two-node
```

## Prerequisites

- [`podman`](https://podman.io/docs/installation) for running the MCP server container
- `JIRA_USERNAME` environment variable set with your Red Hat email
- `JIRA_API_TOKEN` environment variable set with a [Jira API token](https://id.atlassian.com/manage-profile/security/api-tokens)

The plugin includes an `.mcp.json` that automatically configures the `mcp-atlassian` MCP server.

## Skills

### `/two-node:create-rhel-stories`

Create OCPEDGE stories for TNF RHEL verification tickets, link them to the RHEL bugs, and set the required components (`Two Node Fencing`, `QE`, `RHEL-Verification`).

```text
# Auto-discover untested tickets
/two-node:create-rhel-stories

# Dry run (preview without changes)
/two-node:create-rhel-stories --dry-run

# Specific tickets
/two-node:create-rhel-stories RHEL-12345 RHEL-12346 RHEL-12347

# JQL query
/two-node:create-rhel-stories jql:project = RHEL AND component = "resource-agents" AND status != Closed
```

**Features:**
- Auto-discovery of untested TNF resource-agents RHEL tickets
- Clone expansion across the full clone tree
- Dry-run mode for previewing without modifying Jira
- Closed story handling (creates new stories for untested tickets)
- Subtask creation (verification + automation)

## Author

lucaconsalvi
