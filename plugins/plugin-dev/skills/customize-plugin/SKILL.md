---
name: customize-plugin
description: "Use when customizing a scaffolded plugin — replacing template files with real implementations. Covers writing SKILL.md files, hook scripts, MCP configs, agent definitions, README, and plugin.json. Trigger when the user has a scaffolded plugin with TODO markers, or says 'customize the plugin', 'fill in the plugin', or 'implement the plugin components'."
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Agent, AskUserQuestion
user-invocable: true
---

# Customize Plugin

Replace scaffolded template files with real implementations. The `marketplace new` command creates generic templates with TODO markers — this skill guides through replacing each component type with production-ready content.

Start by reading the scaffolded plugin to understand what components exist:

```bash
find plugins/<name> -type f
```

Then customize each component present.

## plugin.json

Verify these fields are correct:

```json
{
  "name": "<must match directory name>",
  "description": "<concise, what it does>",
  "version": "1.0.0",
  "author": { "name": "<github-handle>" },
  "homepage": "https://github.com/openshift-eng/edge-tooling",
  "license": "Apache-2.0"
}
```

## README.md

Replace the template entirely. Include:

- One-line description of what the plugin does
- Prerequisites table (tools, env vars, credentials)
- Installation: always `/plugin marketplace add openshift-eng/edge-tooling`
- Usage instructions specific to the plugin's components
- Author

Keep it short. See `plugins/mcp-atlassian/README.md` for a clean example.

## Skills (SKILL.md)

The template is OpenShift-focused boilerplate. Replace it entirely.

### Frontmatter

```yaml
---
name: <skill-name>
description: "<when to trigger — be specific and slightly pushy about trigger conditions>"
allowed-tools: <comma-separated list of tools the skill needs>
user-invocable: true|false
---
```

The `description` is the primary trigger mechanism — Claude decides whether to invoke a skill based on this field. Include both what the skill does AND specific phrases/contexts that should trigger it. Err on the side of being explicit about triggers because Claude tends to under-trigger.

Set `user-invocable: false` for sub-skills that are only called by orchestrator skills or spawned as sub-agents.

### Body

Structure the body as a workflow:

1. **Purpose** — one paragraph explaining what this skill does and why
2. **Prerequisites** — what must be true before the skill runs
3. **Workflow steps** — numbered steps with clear instructions
4. **Edge cases** — known gotchas and how to handle them
5. **Example** — at least one realistic example showing trigger and expected flow

Explain the *why* behind instructions rather than heavy-handed MUSTs. Claude follows reasoning better than rigid rules.

See `plugins/edge-scrum/skills/create-epic/SKILL.md` for a well-structured example with config, workflow steps, and edge cases.

### Orchestrator Skills

If the skill orchestrates sub-skills via sub-agents:

- Set `allowed-tools` to include `Agent`
- Define which sub-skills to spawn and in what order
- Specify what context to pass to each sub-agent
- Handle sequential dependencies (scaffold before customize) vs parallel opportunities (independent customizations)
- Set sub-skill `user-invocable: false` if they should only be called by the orchestrator

## Hooks (hooks.json + scripts)

### hooks.json

The template has an empty matcher. Replace with actual event binding:

```json
{
  "hooks": {
    "<Event>": [
      {
        "matcher": "<tool-or-event-pattern>",
        "hooks": [
          {
            "type": "command",
            "command": "plugins/<plugin-name>/hooks/<script>.sh",
            "timeout": 10,
            "statusMessage": "Running check..."
          }
        ]
      }
    ]
  }
}
```

Common events and matchers:

| Event | Matcher | Use Case |
|-------|---------|----------|
| `PostToolUse` | `Write\|Edit` | Lint/validate files after changes |
| `PostToolUse` | `Bash` | Check command output |
| `SessionStart` | `startup\|resume` | Environment checks at session start |

### Hook Scripts

Hook scripts receive JSON on stdin with tool context. Key patterns:

```bash
#!/usr/bin/env bash
set -euo pipefail

input=$(cat)
file_path=$(echo "$input" | jq -r '.tool_input.file_path // empty')

# Early exit if not relevant
[[ -z "$file_path" ]] && exit 0
[[ "$file_path" != *.ext ]] && exit 0

# Run check
output=$(some-linter "$file_path" 2>&1) && exit 0

# Block on failure
jq -n --arg reason "Check failed:
$output" '{"decision":"block","reason":$reason}'
```

The script's `command` path in hooks.json is relative to the repo root.

Make the script executable: `chmod +x plugins/<name>/hooks/<script>.sh`

## MCP (.mcp.json)

Configure the actual MCP server. Common pattern for container-based servers:

```json
{
  "mcpServers": {
    "<server-name>": {
      "command": "podman",
      "args": ["run", "--rm", "-i", "-e", "VAR_NAME", "<image>"],
      "env": {
        "VAR_NAME": "${VAR_NAME}"
      }
    }
  }
}
```

The server name determines the tool namespace: `mcp__plugin_<plugin-name>_<server-name>__<tool>`. If other plugins' skills reference these tools in `allowed-tools`, those references need the full namespace.

## Agents

Define the agent's role and constraints in markdown:

```markdown
# Agent Name

You are a specialized agent for [purpose].

## Tools Available
- Tool 1: for X
- Tool 2: for Y

## Instructions
[What the agent should do, step by step]

## Output Format
[What to return to the caller]
```
