# Hooks

[← AI Best Practices](../../README.md) · [Building AI Tools](README.md)

Hooks are event-driven scripts that run in response to Claude Code lifecycle events. They execute outside the context window, costing zero tokens unless they return output. Use them for deterministic automation that should happen transparently -- session startup checks, safety guards, and environment validation.

## When to Use

- **Session startup checks** -- validate environment, check dependencies, detect stale state
- **Safety guards** -- block dangerous commands, validate tool inputs before execution
- **Automated responses** -- inject context based on events without user interaction

**Don't use hooks for:**

- Complex multi-step workflows (use [skills](skills.md))
- User-invocable behavior (use [skills](skills.md) or [commands](plugins.md))
- Anything that requires back-and-forth with the user
- Long-running operations (hooks block the event they respond to)

## Anatomy

Hooks are configured in `.claude/settings.json` under the `hooks` key. Each event type maps to an array of hook groups, where each group has a `matcher` and a `hooks` array.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/update-submodules.sh",
            "timeout": 30,
            "statusMessage": "Checking for submodule updates..."
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/validate-bash.sh",
            "timeout": 5,
            "statusMessage": "Validating command..."
          }
        ]
      }
    ]
  }
}
```

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | Always `"command"` for script-based hooks |
| `command` | string | Yes | Path to the script, relative to repo root |
| `timeout` | number | No | Max execution time in seconds. Default varies by event. |
| `statusMessage` | string | No | Displayed to the user while the hook runs |

### Matcher

The `matcher` field filters when a hook group fires. Its meaning depends on the event type:

| Event | Matcher matches against |
|-------|------------------------|
| `SessionStart` | `"startup"` (new session) or `"resume"` (continued session). Use `"startup\|resume"` for both. |
| `PreToolUse` / `PostToolUse` | Tool name (e.g., `"Bash"`, `"Edit"`, `"Write"`, `"mcp__*"`). Supports regex. |
| `Stop` | Not applicable (fires on every stop) |
| `UserPromptSubmit` | Not applicable (fires on every user message) |

## Event Types

| Event | When It Fires | Use Cases |
|-------|---------------|-----------|
| `SessionStart` | Session begins or resumes | Environment checks, dependency validation, stale state detection |
| `PreToolUse` | Before a tool executes | Block dangerous commands, validate inputs, enforce conventions |
| `PostToolUse` | After a tool executes | Log actions, trigger follow-up checks, audit trail |
| `Stop` | Claude finishes a response | Post-completion validation, summary injection |
| `UserPromptSubmit` | User submits a prompt | Input preprocessing, context injection |

## Hook Script Pattern

Hook scripts receive JSON on stdin describing the event. They can output JSON to inject context back into the conversation or block the action.

```bash
#!/bin/bash
set -euo pipefail

# Require jq for JSON parsing
if ! command -v jq &>/dev/null; then
    echo "Error: jq is required but not installed." >&2
    exit 1
fi

# Read event payload from stdin
INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

# --- Your logic here ---

# Output: return hookSpecificOutput JSON to inject context
cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "Summary of what was detected and what Claude should do about it."
  }
}
EOF

exit 0
```

### Exit Codes

| Code | Behavior |
|------|----------|
| `0` | Hook succeeded. If JSON output is present, it's injected into context. |
| Non-zero | Hook failed. For `PreToolUse`, this **blocks** the tool call. For other events, the error is logged. |

### Blocking a Tool Call (PreToolUse)

For `PreToolUse` hooks, exit with a non-zero code and write a reason to stderr to block the action:

```bash
#!/bin/bash
set -euo pipefail
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.toolInput.command // empty')

if echo "$COMMAND" | grep -q "rm -rf /"; then
    echo "Blocked: destructive command detected" >&2
    exit 1
fi

exit 0
```

## Do's

- **Keep hooks fast.** Target under 5 seconds for `SessionStart`, under 1 second for `PreToolUse`. Hooks block the event they respond to.
- **Explain when blocking.** Write a clear reason to stderr so the user understands why an action was prevented.
- **Use `SessionStart` for environment checks.** Validate dependencies, detect stale state, check connectivity. These checks run once and save debugging time later.
- **Validate dependencies early.** Check for required tools (`jq`, `git`, `curl`) at the top of the script and fail with an actionable error message.
- **Read input defensively.** Use `jq -r '.field // empty'` to handle missing fields without crashing.
- **Exit cleanly.** Always `exit 0` on success. Reserve non-zero exits for intentional blocks (`PreToolUse`) or genuine failures.

## Don'ts

- **Don't use hooks for complex workflows.** If your hook has more than ~50 lines of logic, it should probably be a skill.
- **Don't block silently.** A hook that exits non-zero without explanation is a debugging nightmare. Always write a reason to stderr.
- **Don't call external APIs synchronously.** Network calls in `PreToolUse` hooks add latency to every tool call. Fetch and cache data in `SessionStart` if needed.
- **Don't duplicate CI checks.** If a validation already runs in CI, don't re-run it in a hook. Hooks are for local, immediate checks.
- **Don't use overly broad matchers.** A `PreToolUse` hook that matches every tool adds latency to the entire session. Scope matchers to the specific tools you need to guard.

## Anti-Patterns

| Anti-Pattern | Problem | Fix |
|-------------|---------|-----|
| **Silent blocker** | Hook exits non-zero with no stderr output. User sees a blocked action with no explanation. | Always write a reason to stderr before exiting non-zero. |
| **Slow gatekeeper** | `PreToolUse` hook calls an external API, adding 2-5 seconds to every tool call. | Move API calls to `SessionStart` and cache results. Or accept the check should run in CI. |
| **CI mirror** | Hook re-runs linting, type checking, or tests that CI already covers. Wastes time locally. | Remove the hook. Let CI handle it. Hooks are for checks CI can't do (local state, environment, submodules). |
| **Overly broad matcher** | Matcher like `".*"` on `PreToolUse` fires on every tool, including reads. | Narrow the matcher to the specific tool (e.g., `"Bash"`, `"Write"`). |
| **Missing dependency check** | Hook assumes `jq` or `git` is installed and crashes with a cryptic error. | Check for required commands at the top with `command -v` and exit with an actionable message. |

## Examples from This Repo

### `update-submodules.sh` -- Stale Submodule Detection

**Event:** `SessionStart` | **Timeout:** 30s

Checks whether git submodules are behind their remote tracking branch. If any are stale, injects context telling Claude how many commits each is behind and what to do about it.

Key patterns:

- Resolves the tracking branch through a fallback chain (`.gitmodules` config, `origin/HEAD`, `main`, `master`)
- Fetches quietly and skips on failure (handles offline gracefully)
- Exits silently when everything is up to date (no noise)
- Outputs structured `hookSpecificOutput` JSON only when action is needed

**Config:**

```json
{
  "matcher": "startup|resume",
  "hooks": [
    {
      "type": "command",
      "command": ".claude/hooks/update-submodules.sh",
      "timeout": 30,
      "statusMessage": "Checking for submodule updates..."
    }
  ]
}
```

### `detect-new-tools.sh` -- Undocumented Tool Detection

**Event:** `SessionStart` | **Timeout:** 10s

Scans for directories that look like tools (contain a `Makefile` or `README.md`) but are not listed in the `DOCUMENTED_TOOLS` array. Notifies Claude to offer a CLAUDE.md update.

Key patterns:

- Maintains an explicit allowlist of documented tools (single place to update)
- Filters out non-tool directories (`.git`, `docs`, `node_modules`, etc.)
- Writes a user-visible notification to stderr and structured context to stdout
- Fast execution -- just filesystem checks, no network calls

**Config:**

```json
{
  "matcher": "startup|resume",
  "hooks": [
    {
      "type": "command",
      "command": ".claude/hooks/detect-new-tools.sh",
      "timeout": 10,
      "statusMessage": "Checking for new tool directories..."
    }
  ]
}
```
