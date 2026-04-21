# MCP Servers

[← AI Best Practices](../../README.md) · [Building AI Tools](README.md)

MCP (Model Context Protocol) servers expose external APIs and services to Claude as callable tools. They provide a standardized interface between AI applications and the systems your team works with daily.

## When to Use

**Use MCP servers when:**

- The team frequently integrates with an external API (Jira, CI systems, product pages)
- Credential management is needed -- API tokens, OAuth, service accounts
- Multiple skills need access to the same service
- The service exposes many related operations that benefit from structured tool schemas

**Don't use MCP servers when:**

- The operation is available via CLI tools (`gh`, `oc`, `curl`, `jq`) -- use `Bash` tool calls instead
- You only need a single operation from a service -- a skill with a script is simpler
- The data is static and can be loaded from a file

## Anatomy

MCP servers are configured in `.mcp.json` at the repo or plugin root. The file contains a `mcpServers` object where each key is the server name.

### Config Structure

```json
{
  "mcpServers": {
    "server-name": {
      "command": "podman",
      "args": [
        "run", "--rm", "-i",
        "-e", "API_URL",
        "-e", "API_TOKEN",
        "ghcr.io/org/server@sha256:abc123..."
      ],
      "env": {
        "API_URL": "https://api.example.com",
        "API_TOKEN": "${API_TOKEN}"
      }
    }
  }
}
```

**Fields:**

| Field | Purpose |
|-------|---------|
| `command` | The executable to run (e.g., `podman`, `node`, `python`) |
| `args` | Arguments passed to the command |
| `env` | Environment variables; `${VAR}` syntax references the user's shell environment |
| `type` | Transport type for remote servers (e.g., `"http"`) |
| `url` | Endpoint URL for HTTP transport servers |
| `headers` | HTTP headers for remote servers |

### Transport Types

| Transport | Use Case | Example |
|-----------|----------|---------|
| **stdio** | Local tools, containers -- server communicates over stdin/stdout | `podman run --rm -i image` |
| **Streamable HTTP** | Remote services supporting HTTP streaming | Remote MCP endpoints |
| **HTTP** | REST-style remote services | `"type": "http", "url": "https://..."` |

For team plugins, prefer stdio with containers. Use HTTP only for shared remote services that are already deployed.

### Tool Schemas

MCP servers expose tools with JSON Schema input validation. Each tool declares its name, description, and `inputSchema`:

```json
{
  "name": "jira_search",
  "description": "Search Jira issues using JQL. Returns issue key, summary, status, and assignee.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "jql": {
        "type": "string",
        "description": "JQL query string (e.g., 'project = OCPEDGE AND status = Open')"
      },
      "max_results": {
        "type": "integer",
        "description": "Maximum number of results to return (default: 50)",
        "default": 50
      }
    },
    "required": ["jql"]
  }
}
```

Claude uses the description and schema to decide when and how to call each tool. Poor descriptions lead to incorrect tool usage.

## Do's

### Use containers for isolation

Run MCP servers in containers via `podman run --rm -i`. This provides process isolation, reproducible environments, and prevents the server from accessing your filesystem.

```json
{
  "command": "podman",
  "args": [
    "run", "--rm", "-i",
    "-e", "JIRA_URL",
    "-e", "JIRA_USERNAME",
    "-e", "JIRA_API_TOKEN",
    "ghcr.io/sooperset/mcp-atlassian@sha256:5cef5042..."
  ]
}
```

### Pin image versions

Use SHA256 digests instead of tags. Tags are mutable -- `latest` today may be different tomorrow. Digests are immutable.

```text
ghcr.io/sooperset/mcp-atlassian@sha256:5cef5042baa79ef1b193d4a6586f3dfd3de251546aa9ee356e4a8a29f7722f7c
```

Not:

```text
ghcr.io/sooperset/mcp-atlassian:latest
```

### Use environment variables for secrets

Never hardcode credentials in `.mcp.json`. Use `${VAR}` syntax to reference environment variables from the user's shell:

```json
{
  "env": {
    "JIRA_URL": "https://redhat.atlassian.net",
    "JIRA_USERNAME": "${JIRA_USERNAME}",
    "JIRA_API_TOKEN": "${JIRA_API_TOKEN}"
  }
}
```

Users set these in their shell profile or Claude Code settings. The `${VAR}` syntax is resolved at runtime by the MCP host.

### Write clear tool schemas

Every tool needs a descriptive `description` and a well-typed `inputSchema`. Include:

- What the tool does and what it returns
- Parameter descriptions with examples
- Default values where appropriate
- Required vs. optional field distinction

### Consider data exposure

MCP server responses are sent to Claude as context. Consider what data each tool returns:

- Filter sensitive fields before returning
- Limit result set sizes to avoid context bloat
- Return concise, relevant information -- not raw API dumps

## Don'ts

### Don't expose destructive endpoints

Don't expose `DELETE`, `DROP`, or other destructive operations without safeguards. If you must expose writes, add confirmation requirements or limit scope to specific resources.

### Don't create servers for simple CLI ops

If `gh pr list` or `oc get pods` does the job, use `Bash` tool calls. MCP servers add overhead -- use them for services that need credential management, complex query interfaces, or multi-tool access patterns.

### Don't skip descriptions

Claude relies on tool descriptions to decide when to use each tool. A tool without a description is effectively invisible. Write descriptions that cover both *what* the tool does and *when* to use it.

### Don't ignore rate limits

Upstream APIs have rate limits. MCP servers should handle rate limiting gracefully -- return informative errors, implement backoff, or document limits for the user. Claude may call tools repeatedly in a loop if not told about constraints.

### Don't return excessive data

Large responses consume context window budget. Design tools to return focused, filtered results. Paginate when necessary. Don't return 500 Jira issues when 10 would suffice.

## Anti-Patterns

| Anti-Pattern | Problem | Better Approach |
|-------------|---------|-----------------|
| Raw database access | Security risk, unrestricted queries | Purpose-built query tools with parameterized inputs |
| No error handling | Server crashes on API failures, Claude retries blindly | Return structured error messages with actionable detail |
| Over-broad tools | Single tool that accepts free-form queries, returns everything | Focused tools per operation (search, get, create, update) |
| Hardcoded credentials | Secrets committed to version control | `${VAR}` syntax in `env` block, user sets in shell |
| Missing documentation | Team can't configure or troubleshoot the server | README with prerequisites, env vars, and tool namespace |

## MCP Protocol Architecture

MCP is an [open standard](https://modelcontextprotocol.io) for connecting AI applications to external systems. Understanding the architecture helps you build better servers.

### Client-Server Model

- **MCP Host** -- the AI application (Claude Code, VS Code, Cursor) that coordinates connections
- **MCP Client** -- a component the host creates per server connection
- **MCP Server** -- your program that provides tools, resources, and prompts

### Server Primitives

| Primitive | Purpose | Discovery |
|-----------|---------|-----------|
| **Tools** | Executable functions (API calls, file ops, queries) | `tools/list` |
| **Resources** | Read-only data sources (file contents, DB records) | `resources/list` |
| **Prompts** | Reusable interaction templates | `prompts/list` |

### Design Principles

- **Dynamic tool discovery** -- clients call `tools/list` to discover available tools. Servers can notify clients when tools change.
- **JSON Schema input validation** -- every tool has an `inputSchema` for type-safe argument validation.
- **Stateful protocol** -- capability negotiation handshake at connection start. Client and server exchange supported capabilities.
- **Token efficiency** -- tools should return concise, relevant information. Don't dump raw API responses.

### Transports

| Transport | Use Case | Auth |
|-----------|----------|------|
| **stdio** | Local tools, containers | N/A (process-level) |
| **Streamable HTTP** | Remote services | OAuth, bearer tokens |

For team plugins, prefer stdio with containers. Use Streamable HTTP only for shared remote services.

## Examples from This Repo

### mcp-atlassian (Jira)

The `mcp-atlassian` plugin runs the [sooperset/mcp-atlassian](https://github.com/sooperset/mcp-atlassian) server in a Podman container. It provides Jira tools -- search, create, update, transition issues -- to any skill or plugin that needs Jira access.

**Config** (`plugins/mcp-atlassian/.mcp.json`):

```json
{
  "mcpServers": {
    "mcp-atlassian": {
      "command": "podman",
      "args": [
        "run", "--rm", "-i",
        "-e", "JIRA_URL",
        "-e", "JIRA_USERNAME",
        "-e", "JIRA_API_TOKEN",
        "-e", "JIRA_SSL_VERIFY",
        "ghcr.io/sooperset/mcp-atlassian@sha256:5cef5042baa79ef1b193d4a6586f3dfd3de251546aa9ee356e4a8a29f7722f7c"
      ],
      "env": {
        "JIRA_URL": "https://redhat.atlassian.net",
        "JIRA_USERNAME": "${JIRA_USERNAME}",
        "JIRA_API_TOKEN": "${JIRA_API_TOKEN}",
        "JIRA_SSL_VERIFY": "true"
      }
    }
  }
}
```

**Key patterns demonstrated:**

- Container isolation via `podman run --rm -i`
- Image pinned to SHA256 digest
- Secrets passed via `${VAR}` environment variable syntax
- Non-secret config (`JIRA_URL`, `JIRA_SSL_VERIFY`) set inline

**Tool namespace when installed as a plugin:**

```text
mcp__plugin_mcp-atlassian_mcp-atlassian__jira_search
mcp__plugin_mcp-atlassian_mcp-atlassian__jira_get_issue
mcp__plugin_mcp-atlassian_mcp-atlassian__jira_create_issue
```

### openshift-ci (CI System)

The `openshift-ci` MCP server provides tools for querying OpenShift CI systems -- payload status, job runs, test failures, release health, and regression analysis. It is a remote HTTP server, not a local container.

**Tool namespace:**

```text
mcp__openshift-ci__get_payload_status
mcp__openshift-ci__get_job_runs
mcp__openshift-ci__get_release_health
mcp__openshift-ci__get_recent_test_failures
```

**Permission configuration** (`.claude/settings.local.json`):

```json
{
  "permissions": {
    "allow": [
      "mcp__openshift-ci__*"
    ]
  }
}
```

Auto-allowing with wildcards is appropriate for read-only MCP servers. For servers that expose write operations, allow specific tools individually.

### productpages (HTTP Transport)

The `productpages` server demonstrates HTTP transport configuration for a remote service:

```json
{
  "mcpServers": {
    "productpages": {
      "type": "http",
      "url": "https://productpages.redhat.com/mcp",
      "headers": {
        "X-MCP-Realm": "urn:mcp:realm:private-core"
      }
    }
  }
}
```

No `command` or `args` needed -- the host connects directly to the remote URL.

## References

- [MCP specification](https://modelcontextprotocol.io) -- full protocol documentation
- [MCP architecture](https://modelcontextprotocol.io/docs/learn/architecture) -- client-server model details
- [MCP server development](https://modelcontextprotocol.io/docs/build/server) -- building your own server
