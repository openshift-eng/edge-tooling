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

### Additional prerequisites for `/two-node:bug-reproducer`

1. Claude Code session open at the **[Two-Node Toolbox (TNT)](https://github.com/openshift-eng/two-node-toolbox) repo** (`two-node-toolbox/deploy/` or `two-node-toolbox/deploy/openshift-clusters/`). Running from any other directory will result in an error.
2. EC2 instance running with `make inventory` completed
3. EC2 configured (`./configure`) and SSH-accessible
4. Pull secret at `deploy/openshift-clusters/roles/dev-scripts/install-dev/files/pull-secret.json` (relative to repo root)

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

### `/two-node:bug-reproducer`

Automated OpenShift bug reproduction for Two-Node with Arbiter (TNA) and Two-Node with Fencing (TNF) topologies.

```text
/two-node:bug-reproducer OCPBUGS-66217
```

One argument: a Jira issue key. The skill handles everything else:

1. **Bug Analysis** -- Fetches the bug from Jira (description + comments), detects topology (arbiter or fencing), classifies bug category, extracts reproduction steps, detects install method (IPI/agent/kcli), and determines the OCP version. Stops if the bug is a test issue (not a product bug) or if the dev-scripts environment cannot reproduce the conditions.
2. **Cluster Deployment** -- Updates the dev-scripts config, uploads day-0 manifests if needed, and runs the Ansible deployment playbook. Monitors deployment every 10 minutes for early failure detection. Cleans and retries on failure (with user approval).
3. **Cluster Ready** -- Waits for all nodes Ready, MCPs updated, and COs healthy. Detects during-install bugs. Applies day-1 manifests if needed.
4. **Bug Reproduction** -- Executes the reproduction steps extracted from the Jira bug on the healthy cluster. This is the core phase for most bugs (post-install steps like pcs commands, node reboots, backup/restore, oc apply, etc.).
5. **Log Collection** -- Collects category-targeted logs (etcd, fencing, MCO, NTO, networking, etc.), rsyncs locally, and generates a findings report.

The cluster is **always left running** after the skill completes so the user can SSH in and inspect.

**Supported topologies:**
- **arbiter** -- Two-Node with Arbiter (TNA): 2 masters + 1 arbiter node
- **fencing** -- Two-Node with Fencing (TNF): 2 masters with BMC-based fencing

**Output:**
- Logs saved to `/tmp/two-node-bug-reproduce-<BUG_ID>/`
- Findings report written to `docs/<bug-id-lowercase>-findings.md` in the TNT repo (e.g., `docs/ocpbugs-66217-findings.md`)
- Cluster left running for manual inspection

## Authors

lucaconsalvi, nhamza
