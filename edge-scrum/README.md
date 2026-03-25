# OpenShift Edge Scrum Tooling

## Jira Configuration Management

Jira metadata (boards, filters, projects, components, labels) is managed as code in `.jira-config/` and synced to Jira automatically via CI.

### How it works

1. Edit the relevant JSON file in `.jira-config/`
2. Update `.jira-config/README.md` to reflect the change (required by CI)
3. Open a PR — CI validates the JSON schema and previews what would be synced
4. On merge to `main`, changes are applied to Jira automatically

> **Note:** Create and delete operations are manual. The sync scripts only update existing entities.

### `.jira-config/` — configuration files

| File | Manages |
|------|---------|
| `boards.json` | Board properties and roadmap feature flags |
| `filters.json` | Filter name, JQL, and share/edit permissions |
| `projects.json` | Project metadata and lead assignment |
| `components.json` | Component names and descriptions |
| `labels.json` | Team label definitions |

See `.jira-config/README.md` for the full inventory of managed entities.

### `.ci/` — automation

| Path | Purpose |
|------|---------|
| `.ci/scripts/validate-configs.sh` | Validates all `.jira-config` JSON files against their schemas |
| `.ci/scripts/validate-readme-sync.sh` | Enforces that README is updated alongside config changes |
| `.ci/scripts/apply-changes.sh` | Detects which configs changed and routes to the appropriate sync script |
| `.ci/scripts/sync-boards.sh` | Syncs board properties via Jira Agile API |
| `.ci/scripts/sync-filters.sh` | Syncs filter metadata via Jira REST API |
| `.ci/scripts/sync-projects.sh` | Syncs project metadata via Jira REST API |
| `.ci/scripts/sync-components.sh` | Syncs component metadata via Jira REST API |
| `.ci/schemas/` | JSON Schema files used by `validate-configs.sh` |

### Running locally

```bash
# Validate config files
edge-scrum/.ci/scripts/validate-configs.sh

# Preview changes that would be synced (no API calls made)
edge-scrum/.ci/scripts/apply-changes.sh --dry-run

# Apply changes
edge-scrum/.ci/scripts/apply-changes.sh
```

Credentials are read from environment variables. `JIRA_URL` defaults to `https://redhat.atlassian.net`:

```bash
export JIRA_USERNAME=<your_email>
export JIRA_API_TOKEN=<your_atlassian_api_token>
```

### Manual full sync

To force-sync all configs regardless of what changed, trigger the **Jira Config — Apply Changes** workflow manually from the GitHub Actions tab (`workflow_dispatch`).

---

## MCP Servers

### Jira MCP Server

In order to utilize jira, you will need to have an Atlassian MCP server configured. In order to configure the MCP server, you will need an API token which you can generate from your [Atlassian Account security tab](https://id.atlassian.com/manage-profile/security/api-tokens).

#### sooperset Atlassian MCP Server Configuration

```json
{
  "mcpServers": {
    "mcp-atlassian": {
      "command": "podman",
      "args": [
        "run",
        "--rm",
        "-i",
        "-e",
        "JIRA_URL",
        "-e",
        "JIRA_USERNAME",
        "-e",
        "JIRA_API_TOKEN",
        "-e",
        "JIRA_SSL_VERIFY",
        "-e",
        "CONFLUENCE_URL",
        "-e",
        "CONFLUENCE_USERNAME",
        "-e",
        "CONFLUENCE_API_TOKEN",
        "-e",
        "CONFLUENCE_SSL_VERIFY",
        "ghcr.io/sooperset/mcp-atlassian:latest"
      ],
      "env": {
        "JIRA_URL": "https://redhat.atlassian.net",
        "JIRA_USERNAME": "<your_email>",
        "JIRA_API_TOKEN": "<your_atlassian_api_token>",
        "JIRA_SSL_VERIFY": "true",
        "CONFLUENCE_URL": "https://redhat.atlassian.net/wiki",
        "CONFLUENCE_USERNAME": "<your_email>",
        "CONFLUENCE_API_TOKEN": "<your_atlassian_api_token>",
        "CONFLUENCE_SSL_VERIFY": "true"
      }
    }
  }
}
```

> **Note:** You can skip the Confluence variables if you don't plan to use the Confluence portion of the MCP server

#### Atlassian Rovo MCP Server Configuration

To run the Atlassian Rovo MCP server, follow [their setup instructions](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/getting-started-with-the-atlassian-remote-mcp-server/).
