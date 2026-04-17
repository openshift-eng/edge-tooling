---
name: scaffold-plugin
description: "Use when scaffolding a new Claude Code plugin for the edge-tooling marketplace. Gathers requirements (name, components, category) and runs `marketplace new` to create the directory structure. Trigger when the user says 'scaffold a plugin', 'marketplace new', or just needs the skeleton created without full customization."
allowed-tools: Bash, AskUserQuestion, Read
user-invocable: true
---

# Scaffold Plugin

Create the directory structure for a new marketplace plugin using the `marketplace new` CLI command.

## Requirements Gathering

Ask the user for anything not already provided:

1. **Plugin name** — lowercase, starts with a letter, only lowercase letters, numbers, and hyphens
2. **Components** — which of these the plugin needs:
   - **Skill** (`--skill`) — a capability Claude can invoke via slash command or auto-trigger
   - **Hook** (`--hook`) — event-driven automation (runs on PostToolUse, SessionStart, etc.)
   - **MCP** (`--mcp`) — external tool server integration (Jira, GitHub, etc.)
   - **Agent** (`--agent`) — subagent definition for specialized/parallel work
3. **Category** — one of: `cluster-ops`, `debug`, `deploy`, `network`, `operator`, `ci-cd`, `util`
4. **Short description** — one line describing what the plugin does
5. **Author** — GitHub handle

## Scaffold

Run the scaffolder with component flags to skip the interactive component selector. The command prompts for category, description, and author:

```bash
./marketplace new <name> [--skill] [--hook] [--mcp] [--agent]
```

If running non-interactively, pipe the answers:

```bash
./marketplace new <name> --hook <<'INPUT'
<category>
<description>
<author>
INPUT
```

## Output

After scaffolding, report what was created and remind the user that all generated files are templates with TODO markers that need to be replaced with real content.

## Valid Plugin Names

Names must match `^[a-z][a-z0-9-]*$`. Good names are descriptive and specific:

- `mcp-atlassian` not `jira`
- `markdownlint` not `linter`
- `shellcheck` not `bash-checker`
