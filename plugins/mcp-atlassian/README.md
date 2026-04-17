# MCP Atlassian

Atlassian Jira MCP server plugin for Claude Code.

Runs the [mcp-atlassian](https://github.com/sooperset/mcp-atlassian) server via Podman, providing Jira tools (search, create, update, transition issues, etc.) to Claude Code plugins and skills.

## Prerequisites

| Requirement | Source |
|-------------|--------|
| Podman | `dnf install podman` / `brew install podman` |
| `JIRA_USERNAME` | Your Atlassian email |
| `JIRA_API_TOKEN` | [Atlassian API tokens](https://id.atlassian.com/manage-profile/security/api-tokens) |

Set environment variables in your shell or Claude Code settings.

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
```

Select `mcp-atlassian` when prompted, or install directly if already added.

## Tool Namespace

When installed as a plugin, tools are namespaced as:

```
mcp__plugin_mcp-atlassian_mcp-atlassian__<tool_name>
```

For example: `mcp__plugin_mcp-atlassian_mcp-atlassian__jira_search`
